"""Explicit, versioned quality configuration; importing never loads ML weights."""
from __future__ import annotations
import copy
import hashlib
import json
import platform
import shutil
from pathlib import Path

# Static source audit at the pinned conversion revision; inference parity is unverified.
HY_ADAPTER_SHA256 = "0a193c9114f57edd1acc513edd20f86125ec23482a449df59d2987976a69187b"

def verify_translation_adapter(path, expected):
    path = Path(path).expanduser().resolve()
    config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    # MLX-LM executes config.model_file, so hashing a different named file is insufficient.
    if config.get("model_file") != "hy_v3.py":
        raise ValueError("Hy config must load exactly the audited hy_v3.py adapter")
    if not expected or file_digest(path / "hy_v3.py") != expected:
        raise ValueError("Hy adapter differs from the configured audited checksum")
    return path

def bandit_state_dict(checkpoint):
    """Normalize the authors' Lightning wrapper, preserving strict model loading."""
    state = checkpoint
    for field in ("state", "state_dict", "model_state_dict"):
        if isinstance(state, dict) and field in state:
            state = state[field]
    if not isinstance(state, dict) or not state or any(not isinstance(key, str) for key in state):
        raise ValueError("BandIt checkpoint has no valid state dictionary")
    if any(key.startswith("model.") for key in state):
        # The published Lightning state also contains training-only loss
        # weights. Discard only identified training namespaces; model keys
        # and shapes still pass PyTorch's strict loading below.
        unexpected = [key for key in state if not key.startswith(("model.", "loss_handler.", "metric_handler."))]
        if unexpected:
            raise ValueError("BandIt checkpoint mixes wrapper and model parameter names")
        return {key.removeprefix("model."): value for key, value in state.items() if key.startswith("model.")}
    return state

MODELS = {
    "fallback": {"repo": "mlx-community/whisper-large-v3-mlx", "runtime": "mlx_whisper", "license": "MIT"},
    "asr": {"repo": "mlx-community/FireRedASR2-AED-mlx", "revision": "f3212eacfa49b851130b97c63653c8e06ee09bdb", "runtime": "mlx_audio", "license": "Apache-2.0", "weights_gb": 4.566},
    "verifier": {"repo": "mlx-community/Qwen3-ASR-1.7B-bf16", "revision": "e1f6c266914abc5a46e8756e02580f834a6cf8a7", "runtime": "mlx_audio", "license": "Apache-2.0", "weights_gb": 4.076},
    "aligner": {"repo": "mlx-community/Qwen3-ForcedAligner-0.6B-bf16", "revision": "53c8c0e46733eec430e4b53dd6471d0e5dee45f8", "runtime": "mlx_audio", "license": "Apache-2.0", "weights_gb": 1.836},
    "translator": {"repo": "QwQbb/Hy-MT2-30B-A3B-MLX-8bit", "revision": "b12a2cb66e79411849a0dfdd044e60cf3e5e4c80", "runtime": "mlx_lm", "license": "upstream Apache-2.0; pinned conversion ships Tencent HY Community License", "weights_gb": 31.944},
    "reviewer": {"repo": "mlx-community/Qwen3.8-27B-8bit", "revision": "815b83c0df8ffd1d1b5244cf75fd6ef14fca9ef9", "runtime": "mlx_vlm", "license": "Apache-2.0", "weights_gb": 29.501},
    "tts": {"repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16", "revision": "52f4770fd9726457eae3d3b6aa92047a25a10776", "runtime": "mlx_audio", "license": "Apache-2.0", "weights_gb": 4.516},
    "diarizer": {"repo": "pyannote/speaker-diarization-community-1", "runtime": "pyannote", "license": "CC-BY-4.0"},
}
DEFAULTS = {
    "models": MODELS, "mlx_python": "", "translation_python": "", "review_python": "", "separator_python": "", "torch_python": "", "clone_python": "",
    "worker_memory_gb": 36.0, "system_memory_gb": 42.0, "worker_timeout": 1800,
    "context_tokens": 4096, "max_output_tokens": 1024, "asr_chunk_seconds": 20,
    "separation_chunk_seconds": 12, "separation_context_seconds": 2,
    "msst_path": "", "bandit_config": "", "bandit_checkpoint": "",
    "bandit_sha256": "", "bandit_config_sha256": "", "separator_device": "auto",
    "translation_model_path": "", "translation_adapter_sha256": HY_ADAPTER_SHA256,
    "review_provider": "local", "cloud_review_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "cloud_review_model": "qwen3.8-max-0902", "allow_cloud": False,
    "voice_engine": "qwen", "qwen_speaker": "Aiden", "american_female_fallback": True,
    "maximum_speed": 1.5, "normal_speed": 1.15, "urgent_speed": 1.25,
    "expressive_speed": 1.10, "allow_rescue_speed": True,
    "on_screen_borrow": 0.25, "off_screen_borrow": 0.6, "speaker_guard": 0.08,
    "qa_word_error_limit": 0.12, "loudness_lufs": -16.0, "true_peak_db": -1.0,
    "series_glossary_path": "", "scene_cuts": [], "off_screen_cues": [],
    "episode_context_path": "",
    "clone_model_dir": "", "clone_config": "", "reference_consent": False,
}

def options(raw=None):
    result = copy.deepcopy(DEFAULTS)
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("quality_settings must be an object")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise ValueError("Unknown quality settings: " + ", ".join(sorted(unknown)))
    for key, value in raw.items():
        if key == "models":
            if not isinstance(value, dict):
                raise ValueError("models must be an object")
            for stage, model in value.items():
                if stage not in MODELS or not isinstance(model, dict):
                    raise ValueError("Unknown quality model stage: " + str(stage))
                if set(model) - {"repo", "revision", "runtime", "license", "weights_gb"}:
                    raise ValueError("Unsupported model metadata fields")
                if "repo" in model and model["repo"] != MODELS[stage]["repo"] and not model.get("revision"):
                    raise ValueError("Custom model repositories require an explicit revision")
                result["models"][stage].update(model)
        else:
            result[key] = value
    for key in ("allow_cloud", "reference_consent", "allow_rescue_speed", "american_female_fallback"):
        if not isinstance(result[key], bool):
            raise ValueError(f"{key} must be an explicit boolean")
    for key, low, high in (("maximum_speed", 1, 1.5), ("normal_speed", 1, 1.15),
                          ("urgent_speed", 1, 1.25), ("expressive_speed", 1, 1.10),
                          ("worker_memory_gb", 1, 36), ("system_memory_gb", 2, 42),
                          ("context_tokens", 256, 4096), ("max_output_tokens", 32, 2048),
                          ("asr_chunk_seconds", 5, 30), ("separation_chunk_seconds", 4, 20),
                          ("separation_context_seconds", 1, 4), ("worker_timeout", 1, 7200),
                          ("qa_word_error_limit", 0, .25), ("loudness_lufs", -24, -14),
                          ("true_peak_db", -6, -1), ("speaker_guard", .04, .2),
                          ("on_screen_borrow", 0, .25), ("off_screen_borrow", 0, .6)):
        if not isinstance(result[key], (int, float)) or not low <= result[key] <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    if result["worker_memory_gb"] >= result["system_memory_gb"]:
        raise ValueError("Worker memory must leave space for the operating system")
    if result["voice_engine"] not in {"qwen", "kokoro", "elevenlabs", "index_clone"}:
        raise ValueError("Unsupported quality voice engine")
    if result["review_provider"] not in {"local", "cloud"}:
        raise ValueError("Unsupported quality review provider")
    if result["qwen_speaker"] not in {"Aiden", "Ryan", "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee"}:
        raise ValueError("Unknown Qwen preset speaker")
    for key in ("scene_cuts", "off_screen_cues"):
        if not isinstance(result[key], list) or any(not isinstance(x, (int, float)) or x < 0 for x in result[key]):
            raise ValueError(f"{key} requires nonnegative numeric values")
    return result

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def setup_issues(settings, *, require_platform=True, mode="dub", analysis=False):
    q = options(settings)
    issues = []
    if require_platform and (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        issues.append("Quality MLX execution requires an Apple Silicon Mac; this host cannot verify it")
    if require_platform and platform.system() == "Darwin":
        try:
            import psutil
            if psutil.virtual_memory().total < 44e9:
                issues.append("The selected 30B/27B quality stack requires at least 48 GB unified memory; use existing providers on smaller Macs")
        except ImportError:
            issues.append("Install psutil in the backend for memory preflight")
    voice_work = mode == "dub" and not analysis
    for exe in (("ffmpeg", "ffprobe", "rubberband") if voice_work else ("ffmpeg", "ffprobe")):
        if not shutil.which(exe):
            issues.append(f"Install {exe} (Rubber Band 4.0.0 with R3 support is required)")
    environments = ("mlx_python", "separator_python", "torch_python") if analysis else ("mlx_python", "translation_python", "review_python", "separator_python", "torch_python")
    for name in environments:
        if not q[name] or not Path(q[name]).is_file():
            issues.append(f"Set {name} to the isolated worker environment's Python")
    for name in ("bandit_config", "bandit_checkpoint"):
        if not q[name] or not Path(q[name]).is_file():
            issues.append(f"Configure {name}")
    if not q["msst_path"] or not (Path(q["msst_path"]) / "inference.py").is_file():
        issues.append("Configure the audited Music-Source-Separation-Training checkout (msst_path)")
    if not q["bandit_sha256"] or not q["bandit_config_sha256"]:
        issues.append("Record audited BandIt checkpoint/config SHA-256 before loading")
    if analysis:
        pass
    elif not q["translation_model_path"] or not q["translation_adapter_sha256"]:
        issues.append("Configure local translation_model_path and audited adapter SHA-256")
    else:
        try:
            verify_translation_adapter(q["translation_model_path"], q["translation_adapter_sha256"])
        except (OSError, ValueError) as exc:
            issues.append("Hy adapter preflight: " + str(exc))
    for key, checksum in (("bandit_checkpoint", "bandit_sha256"), ("bandit_config", "bandit_config_sha256")):
        if q[key] and Path(q[key]).is_file() and q[checksum] and file_digest(q[key]) != q[checksum]:
            issues.append(f"{key} does not match its audited checksum")
    if (not analysis and q["review_provider"] == "cloud") or (voice_work and q["voice_engine"] == "elevenlabs"):
        if not q["allow_cloud"]:
            issues.append("Enable explicit cloud text upload consent for the selected cloud provider")
    if voice_work and q["voice_engine"] == "index_clone":
        if not q["reference_consent"]:
            issues.append("Explicit reference voice consent is required")
        for name in ("clone_python", "clone_model_dir", "clone_config"):
            if not q[name] or not Path(q[name]).exists():
                issues.append(f"Configure {name} for IndexTTS2.5")
    return issues
