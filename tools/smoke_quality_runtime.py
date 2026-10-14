"""Native runtime/API smoke tests without downloading or claiming model inference."""
import argparse
import importlib
import inspect
import json
import platform

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["audio", "translation", "review", "separator", "torch"])
    args = parser.parse_args()
    result = {"role": args.role, "machine": platform.machine(), "model_inference_verified": False}
    if args.role in {"audio", "translation", "review"}:
        import mlx.core as mx
        x = mx.ones((4, 4))
        mx.eval(x @ x)
        assert float((x @ x)[0, 0].item()) == 4
        result["mlx_default_device"] = str(mx.default_device())
        assert callable(mx.set_memory_limit) and callable(mx.set_cache_limit)
    if args.role == "audio":
        from mlx_audio.stt.utils import load_model
        for path in ("mlx_audio.stt.models.fireredasr2.fireredasr2", "mlx_audio.stt.models.qwen3_asr.qwen3_asr",
                     "mlx_audio.stt.models.qwen3_forced_aligner"):
            module = importlib.import_module(path)
            # Qwen's public Model delegates to its ASR/aligner implementation;
            # the generation method is an instance attribute through __getattr__.
            implementation = getattr(module, "Qwen3ASRModel", module.Model)
            assert hasattr(implementation, "generate"), path
            if implementation is not module.Model:
                assert callable(module.Model.__getattr__)
        module = importlib.import_module("mlx_audio.tts.models.qwen3_tts")
        assert hasattr(module.Model, "generate_custom_voice")
        import mlx_whisper
        assert callable(mlx_whisper.transcribe)
    elif args.role == "translation":
        from mlx_lm import load, generate
        from mlx_lm.sample_utils import make_sampler
        assert callable(load) and callable(generate) and callable(make_sampler(temp=.7))
    elif args.role == "review":
        from mlx_vlm import load, generate
        from mlx_vlm.prompt_utils import apply_chat_template
        importlib.import_module("mlx_vlm.models.qwen3_5")
        assert callable(load) and callable(generate) and callable(apply_chat_template)
    else:
        import torch
        assert torch.ones((2, 2)).sum().item() == 4
        result["mps_available"] = bool(torch.backends.mps.is_available())
        if args.role == "torch":
            from pyannote.audio import Pipeline
            from kokoro import KPipeline
            assert "token" in inspect.signature(Pipeline.from_pretrained).parameters
            assert callable(KPipeline)
        else:
            import pytorch_lightning
            from omegaconf import OmegaConf
            assert OmegaConf.create({"a": 1}).a == 1
    print(json.dumps(result, indent=2))

if __name__ == "__main__": main()
