"""Isolated model RPC executable. One process owns one stage and releases it on exit."""
from __future__ import annotations
import contextlib
import dataclasses
import json
import os
from pathlib import Path
import sys
import traceback
import importlib.metadata
import threading
import time

_loaded = None
_identity = None

def primitive(value):
    if dataclasses.is_dataclass(value):
        return primitive(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    raise TypeError(f"Unsupported model result: {type(value).__name__}")

def snapshot(model, q, role):
    from huggingface_hub import snapshot_download
    if role == "translator":
        from .quality_models import verify_translation_adapter
        path = verify_translation_adapter(q["translation_model_path"], q["translation_adapter_sha256"])
        return str(path)
    from tqdm.auto import tqdm
    class DownloadProgress(tqdm):
        def display(self, *args, **kwargs):
            now = time.monotonic()
            if now - getattr(self, "_last_event", 0) >= .5 or self.n == self.total:
                self._last_event = now
                print("DUBCANVAS_DOWNLOAD " + json.dumps({"model": model["repo"],
                    "completed_files": self.n, "total_files": self.total or 0}), file=sys.stderr, flush=True)
    return snapshot_download(model["repo"], revision=model.get("revision"), tqdm_class=DownloadProgress,
                             allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.tiktoken", "*.npz", "*.npy", "*.ark", "*.yaml"])

def load(role, q):
    global _loaded, _identity
    identity = json.dumps([role, q["models"].get(role)], sort_keys=True)
    if _identity is not None and identity != _identity:
        raise ValueError("A worker cannot retain two different model stages")
    if _loaded is not None:
        return _loaded
    if role in {"asr", "verifier", "aligner", "tts", "translator", "reviewer"}:
        import mlx.core as mx
        mx.set_memory_limit(int(q["worker_memory_gb"] * 1e9))
        mx.set_cache_limit(256 * 1024 * 1024)
        model_path = snapshot(q["models"][role], q, role)
        if role in {"asr", "verifier", "aligner"}:
            from mlx_audio.stt.utils import load_model
            _loaded = load_model(model_path)
        elif role == "tts":
            from mlx_audio.tts.utils import load_model
            _loaded = load_model(model_path)
        elif role == "translator":
            from mlx_lm import load as load_lm
            _loaded = load_lm(model_path, tokenizer_config={"trust_remote_code": False})
        else:
            from mlx_vlm import load as load_vlm
            m, processor = load_vlm(model_path)
            _loaded = (m, processor, json.loads((Path(model_path) / "config.json").read_text(encoding="utf-8")))
    elif role == "diarizer":
        from pyannote.audio import Pipeline
        _loaded = Pipeline.from_pretrained(q["models"][role]["repo"], token=os.getenv("HF_TOKEN"))
        import torch
        _loaded.to(torch.device("cpu"))
    elif role == "separator":
        from .quality_models import file_digest
        for key, expected in (("bandit_checkpoint", "bandit_sha256"), ("bandit_config", "bandit_config_sha256")):
            if file_digest(q[key]) != q[expected]:
                raise ValueError(f"Audited {key} checksum mismatch")
        sys.path.insert(0, q["msst_path"])
        from models.bandit_v2.bandit import Bandit
        from omegaconf import OmegaConf
        import torch
        import yaml
        # The audited upstream YAML contains a !!python/tuple augmentation entry.
        config = OmegaConf.create(yaml.load(Path(q["bandit_config"]).read_text(encoding="utf-8"), Loader=yaml.FullLoader))
        model = Bandit(**dict(config.kwargs))
        state = torch.load(q["bandit_checkpoint"], map_location="cpu", weights_only=True)
        for key in ("state", "state_dict", "model_state_dict"):
            if key in state:
                state = state[key]
        model.load_state_dict(state, strict=True)
        requested = q["separator_device"]
        device = ("mps" if torch.backends.mps.is_available() else "cpu") if requested == "auto" else requested
        if device not in {"mps", "cpu"}:
            raise ValueError("Separator device must be auto, mps or cpu")
        _loaded = (model.to(device).eval(), config, device)
    elif role == "index_clone":
        from indextts.infer_v2_5 import IndexTTS2
        _loaded = IndexTTS2(cfg_path=q["clone_config"], model_dir=q["clone_model_dir"],
                           use_bf16=False, use_cuda_kernel=False, use_deepspeed=False)
    elif role == "kokoro":
        from kokoro import KPipeline
        _loaded = KPipeline(lang_code="a")
    elif role == "fallback":
        import mlx_whisper
        _loaded = mlx_whisper
    else:
        raise ValueError("Unknown worker role: " + role)
    _identity = identity
    return _loaded

def execute(role, request, q):
    model = load(role, q)
    if role in {"asr", "verifier"}:
        kwargs = {"beam_size": 3} if role == "asr" else {
            "language": request.get("language"), "max_tokens": 1024,
            "hotwords": request.get("hotwords", []), "chunk_duration": 30, "min_chunk_duration": .1}
        result = primitive(model.generate(request["audio"], **kwargs))
        return result
    if role == "fallback":
        return primitive(model.transcribe(request["audio"], path_or_hf_repo=q["models"]["fallback"]["repo"],
                                          language="zh", task="transcribe", word_timestamps=True))
    if role == "aligner":
        return primitive(model.generate(request["audio"], text=request["text"], language=request["language"]))
    if role == "diarizer":
        import soundfile as sf
        import torch
        samples, sample_rate = sf.read(request["audio"], dtype="float32", always_2d=True)
        result = model({"waveform": torch.from_numpy(samples.T.copy()), "sample_rate": sample_rate})
        diarization = getattr(result, "speaker_diarization", result)
        return {"tracks": [{"start": s.start, "end": s.end, "speaker": str(label)}
                            for s, _, label in diarization.itertracks(yield_label=True)]}
    if role in {"translator", "reviewer"}:
        m, tokenizer = model[:2]
        tokens = q["max_output_tokens"]
        prompt = request["prompt"]
        messages = [{"role": "user", "content": prompt}]
        if role == "translator":
            from mlx_lm import generate
            from mlx_lm.sample_utils import make_sampler
            rendered = tokenizer.apply_chat_template(messages, add_generation_prompt=True,
                                                      tokenize=False)
            if len(tokenizer.encode(rendered)) + tokens > q["context_tokens"]:
                raise ValueError("Prompt exceeds configured context budget; shorten scene context")
            result = generate(m, tokenizer, prompt=rendered, max_tokens=tokens,
                              sampler=make_sampler(temp=.7, top_p=1.0), verbose=False)
            return {"text": str(result)}
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        rendered = apply_chat_template(tokenizer, model[2], prompt, num_images=0,
                                       enable_thinking=False)
        tok = getattr(tokenizer, "tokenizer", tokenizer)
        if len(tok.encode(rendered)) + tokens > q["context_tokens"]:
            raise ValueError("Reviewer prompt exceeds configured context budget")
        result = generate(m, tokenizer, prompt=rendered, max_tokens=tokens, temperature=0.0,
                          verbose=False)
        return {"text": str(getattr(result, "text", result))}
    if role == "tts":
        import numpy as np
        import soundfile as sf
        audio = []
        sr = None
        for result in model.generate_custom_voice(text=request["text"], speaker=request["speaker"],
                                                  language="English", instruct=request["instruct"]):
            audio.append(np.asarray(result.audio, dtype=np.float32).reshape(-1))
            sr = int(result.sample_rate)
        if not audio or not sr:
            raise ValueError("Qwen TTS returned no samples")
        sf.write(request["output"], np.concatenate(audio), sr, subtype="FLOAT")
        return {"output": request["output"], "sample_rate": sr}
    if role == "kokoro":
        import numpy as np
        import soundfile as sf
        chunks = []
        for result in model(request["text"], voice=request["voice"], speed=1.0):
            samples = result.audio
            if hasattr(samples, "detach"):
                samples = samples.detach().cpu().numpy()
            chunks.append(np.asarray(samples, dtype=np.float32).reshape(-1))
        if not chunks:
            raise ValueError("Kokoro returned no samples")
        sf.write(request["output"], np.concatenate(chunks), 24000, subtype="FLOAT")
        return {"output": request["output"], "native_acceleration": 1.0}
    if role == "index_clone":
        if not q["reference_consent"] or not request.get("reference"):
            raise ValueError("Cloning requires explicit consent and a valid reference")
        # Keep native articulation at normal rate. DSP alone enforces the speed cap.
        model.infer(spk_audio_prompt=request["reference"], text=request["text"],
                    output_path=request["output"], lang="en", duration_factor=1.0)
        return {"output": request["output"], "native_acceleration": 1.0}
    if role == "separator":
        from .bandit_inference import separate_window
        m, config, device = model
        return separate_window(m, config, device, request["input"], request["output"])
    raise ValueError("Unimplemented worker role")

def main():
    role = sys.argv[1]
    import psutil
    parent = psutil.Process(os.getppid())
    parent_created = parent.create_time()
    def watch_parent():
        while True:
            time.sleep(.5)
            try:
                if not parent.is_running() or parent.create_time() != parent_created:
                    os._exit(1)
            except psutil.Error:
                os._exit(1)
    threading.Thread(target=watch_parent, daemon=True).start()
    versions = {}
    for package in ("mlx", "mlx-lm", "mlx-vlm", "mlx-audio", "torch", "torchaudio", "pyannote-audio", "kokoro"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    print("DUBCANVAS_METADATA " + json.dumps({"role": role, "python": sys.version, "versions": versions}), flush=True)
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                result = execute(role, payload["request"], payload["settings"])
            if "mlx.core" in sys.modules:
                mx = sys.modules["mlx.core"]
                counters = {}
                for name in ("get_peak_memory", "get_active_memory", "get_cache_memory"):
                    if callable(getattr(mx, name, None)):
                        counters[name] = int(getattr(mx, name)())
                print("DUBCANVAS_METADATA " + json.dumps({"role": role,
                    "mlx_allocator_bytes": counters,
                    "note": "MLX allocator counters, not system physical footprint"}), flush=True)
            output = {"ok": True, "result": result}
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            output = {"ok": False, "error": str(exc)}
        print("DUBCANVAS_RESULT " + json.dumps(output, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
