from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional


_MODEL_LOCK = threading.RLock()
_CHATTERBOX_MODELS: dict[tuple[str, bool], object] = {}
_MULTILINGUAL_MODELS: dict[str, object] = {}
_KOKORO_PIPELINES: dict[str, object] = {}
MIN_CHATTERBOX_REFERENCE_SECONDS = 5.25  # Chatterbox requires strictly more than five seconds.


def reference_audio_duration(path: str | Path) -> float:
    """Measure a reference before loading Chatterbox; supports WAV and common compressed formats."""
    path = Path(path).expanduser()
    if not path.is_file():
        return 0.0
    try:
        import soundfile as sf
        info = sf.info(str(path))
        if info.frames > 0 and info.samplerate > 0:
            return info.frames / info.samplerate
    except (OSError, RuntimeError, ImportError, ValueError):
        pass
    if shutil.which("ffprobe"):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            try:
                return float(result.stdout.strip())
            except ValueError:
                pass
    return 0.0


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def piper_executable() -> Optional[str]:
    found = shutil.which("piper")
    if found:
        return found
    # When callers run .venv/bin/python directly without activating the venv,
    # its scripts directory is not necessarily on PATH.
    name = "piper.exe" if os.name == "nt" else "piper"
    sibling = Path(sys.executable).resolve().parent / name
    return str(sibling) if sibling.exists() else None


def piper_command() -> list[str] | None:
    exe = piper_executable()
    if exe:
        return [exe]
    try:
        if importlib.util.find_spec("piper") is not None:
            return [sys.executable, "-m", "piper"]
    except Exception:
        pass
    return None


def piper_available() -> bool:
    return piper_command() is not None


def chatterbox_available() -> bool:
    return _module_available("chatterbox") and _module_available("torchaudio")


def multilingual_chatterbox_available() -> bool:
    return chatterbox_available() and _module_available("chatterbox.mtl_tts")


def kokoro_available() -> bool:
    return _module_available("kokoro") and _module_available("soundfile")


def premium_voice_status() -> dict:
    return {
        "chatterbox": chatterbox_available(),
        "chatterbox_multilingual": multilingual_chatterbox_available(),
        "kokoro": kokoro_available(),
        "piper": piper_available(),
    }


def resolve_piper_model(explicit: str = "") -> Path:
    raw = (explicit or os.getenv("PIPER_MODEL", "")).strip()
    if not raw:
        raise RuntimeError(
            "Piper is selected but no voice model was configured. "
            "Pass --piper-model /path/to/voice.onnx or set PIPER_MODEL."
        )
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"Piper model does not exist: {path}")
    return path


def synthesize_piper(
    text: str,
    out_wav: Path,
    *,
    model_path: str,
    rate: int = 205,
    speaker: int | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Synthesize with Piper through the stable CLI/stdin interface."""
    base_cmd = piper_command()
    if not base_cmd:
        raise RuntimeError(
            "Piper was not found. Install piper-tts in the environment "
            "or choose another TTS provider."
        )
    model = resolve_piper_model(model_path)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if cancel_check:
        cancel_check()

    safe_rate = max(80, min(450, int(rate)))
    length_scale = max(0.55, min(1.80, 205.0 / safe_rate))
    cmd = [
        *base_cmd,
        "--model", str(model),
        "--output_file", str(out_wav),
        "--length_scale", f"{length_scale:.4f}",
    ]
    if speaker is not None:
        cmd += ["--speaker", str(int(speaker))]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _stdout, stderr = proc.communicate(text + "\n")
    except BaseException:
        try:
            proc.terminate()
        except Exception:
            pass
        raise
    if cancel_check:
        cancel_check()
    if proc.returncode != 0:
        raise RuntimeError(f"Piper failed ({proc.returncode}): {(stderr or '')[-2000:]}")
    if not out_wav.exists() or out_wav.stat().st_size <= 44:
        raise RuntimeError("Piper completed without producing usable audio.")


def _best_torch_device(requested: str = "auto") -> str:
    requested = (requested or "auto").strip().lower()
    if requested != "auto":
        return requested

    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if (
            platform.system() == "Darwin"
            and hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            return "mps"
    except Exception:
        pass
    return "cpu"


def _prepare_chatterbox_watermarker() -> str:
    """Work around a known Perth packaging failure seen on Apple Silicon.

    Some resemble-perth installs expose PerthImplicitWatermarker as None when
    its optional neural-watermarker import fails. Chatterbox constructs that
    symbol unconditionally, which otherwise crashes model initialization with
    "'NoneType' object is not callable". When that happens, use Perth's own
    DummyWatermarker so speech generation can continue instead of failing.
    """
    try:
        import perth
    except Exception:
        return "unavailable"

    implicit = getattr(perth, "PerthImplicitWatermarker", None)
    if callable(implicit):
        return "implicit"

    dummy = getattr(perth, "DummyWatermarker", None)
    if callable(dummy):
        perth.PerthImplicitWatermarker = dummy
        return "dummy"

    return "missing"


def _load_chatterbox(device: str, turbo: bool):
    key = (device, turbo)
    with _MODEL_LOCK:
        model = _CHATTERBOX_MODELS.get(key)
        if model is not None:
            return model

        # Chatterbox currently assumes PerthImplicitWatermarker is callable.
        # On some macOS/Apple-Silicon installations resemble-perth exports that
        # symbol as None. Patch it to Perth's documented dummy implementation
        # before importing/constructing Chatterbox.
        watermarker_mode = _prepare_chatterbox_watermarker()

        def construct(target_device: str):
            if turbo:
                from chatterbox.tts_turbo import ChatterboxTurboTTS

                return ChatterboxTurboTTS.from_pretrained(device=target_device)
            from chatterbox.tts import ChatterboxTTS

            return ChatterboxTTS.from_pretrained(device=target_device)

        try:
            model = construct(device)
        except Exception as exc:
            # MPS support in the upstream stack is still less complete than
            # CUDA/CPU. If model initialization itself fails on MPS, retry on
            # CPU rather than killing a long dubbing job.
            if device == "mps":
                try:
                    model = construct("cpu")
                    _CHATTERBOX_MODELS[("cpu", turbo)] = model
                except Exception as cpu_exc:
                    kind = "Turbo" if turbo else "standard"
                    raise RuntimeError(
                        f"Could not load Chatterbox {kind} on mps ({exc}); "
                        f"CPU fallback also failed ({cpu_exc}). "
                        f"Perth watermarker mode: {watermarker_mode}"
                    ) from cpu_exc
            else:
                kind = "Turbo" if turbo else "standard"
                raise RuntimeError(
                    f"Could not load Chatterbox {kind} on {device}: {exc}. "
                    f"Perth watermarker mode: {watermarker_mode}"
                ) from exc

        _CHATTERBOX_MODELS[key] = model
        return model


def synthesize_chatterbox(
    text: str,
    out_wav: Path,
    *,
    reference_audio: str = "",
    expressiveness: float = 0.5,
    device: str = "auto",
    turbo: bool = True,
    american_english: bool = False,
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Generate high-quality English speech with Chatterbox.

    A reference clip is optional. It can be selected by the user or extracted
    from clean source dialogue during character analysis.
    """
    if not chatterbox_available():
        raise RuntimeError(
            "Chatterbox is not installed. Run macos/install_voice_engines.sh "
            "or install chatterbox-tts in the DubCanvas environment."
        )

    if cancel_check:
        cancel_check()

    ref = str(reference_audio or "").strip()
    if ref:
        ref_path = Path(ref).expanduser().resolve()
        if not ref_path.exists():
            raise RuntimeError(f"Chatterbox reference clip does not exist: {ref_path}")
        duration = reference_audio_duration(ref_path)
        if duration < MIN_CHATTERBOX_REFERENCE_SECONDS:
            raise RuntimeError(
                f"Chatterbox needs more than 5 seconds of reference speech; "
                f"{ref_path.name} is {duration:.2f}s. Choose a longer clip."
            )
        ref = str(ref_path)

    import torchaudio as ta

    # Turbo does not support CFG. Standard Chatterbox requires two text sequences
    # even when guidance is effectively disabled: its inference always adds a
    # two-sequence BOS embedding. Exactly zero creates only one text sequence
    # and fails with a 1-vs-2 tensor size mismatch upstream.
    use_turbo = turbo and not (american_english and ref)
    resolved_device = _best_torch_device(device)
    model = _load_chatterbox(resolved_device, use_turbo)

    exaggeration = max(0.0, min(1.5, float(expressiveness)))
    kwargs = {} if use_turbo else {"exaggeration": exaggeration}
    if american_english and ref:
        kwargs["cfg_weight"] = 1e-6
    if ref:
        kwargs["audio_prompt_path"] = ref

    with _MODEL_LOCK:
        if cancel_check:
            cancel_check()
        wav = model.generate(str(text), **kwargs)

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    ta.save(str(out_wav), wav.detach().cpu(), int(model.sr))
    if cancel_check:
        cancel_check()
    if not out_wav.exists() or out_wav.stat().st_size <= 44:
        raise RuntimeError("Chatterbox completed without producing usable audio.")


def synthesize_multilingual_chatterbox(
    text: str, out_wav: Path, *, language: str, reference_audio: str = "",
    expressiveness: float = 0.5, device: str = "auto",
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Dub in a supported target language with optional character voice reference."""
    if not multilingual_chatterbox_available():
        raise RuntimeError("Chatterbox Multilingual is not installed. Update chatterbox-tts or choose ElevenLabs.")
    if cancel_check:
        cancel_check()
    ref = str(reference_audio or "").strip()
    if ref:
        ref_path = Path(ref).expanduser().resolve()
        if not ref_path.is_file() or reference_audio_duration(ref_path) < MIN_CHATTERBOX_REFERENCE_SECONDS:
            raise RuntimeError("Chatterbox needs a valid reference with more than five seconds of speech.")
        ref = str(ref_path)

    import torchaudio as ta
    device = _best_torch_device(device)
    with _MODEL_LOCK:
        model = _MULTILINGUAL_MODELS.get(device)
        if model is None:
            _prepare_chatterbox_watermarker()
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            try:
                model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
            except TypeError:
                model = ChatterboxMultilingualTTS.from_pretrained(device=device)
            _MULTILINGUAL_MODELS[device] = model
        if cancel_check:
            cancel_check()
        kwargs = {"language_id": language, "exaggeration": max(0.0, min(1.5, float(expressiveness)))}
        if ref:
            kwargs["audio_prompt_path"] = ref
            # Resemble's documented mitigation for transferring a source accent.
            kwargs["cfg_weight"] = 0.0
        wav = model.generate(str(text), **kwargs)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    ta.save(str(out_wav), wav.detach().cpu(), int(model.sr))
    if cancel_check:
        cancel_check()
    if not out_wav.exists() or out_wav.stat().st_size <= 44:
        raise RuntimeError("Chatterbox Multilingual completed without producing usable audio.")


def _load_kokoro(lang_code: str = "a"):
    with _MODEL_LOCK:
        pipeline = _KOKORO_PIPELINES.get(lang_code)
        if pipeline is not None:
            return pipeline
        try:
            from kokoro import KPipeline

            pipeline = KPipeline(lang_code=lang_code)
        except Exception as exc:
            raise RuntimeError(f"Could not load Kokoro: {exc}") from exc
        _KOKORO_PIPELINES[lang_code] = pipeline
        return pipeline


def synthesize_kokoro(
    text: str,
    out_wav: Path,
    *,
    voice: str = "af_heart",
    rate: int = 205,
    lang_code: str = "a",
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Generate fast local English speech with Kokoro-82M."""
    if not kokoro_available():
        raise RuntimeError(
            "Kokoro is not installed. Run macos/install_voice_engines.sh "
            "or install kokoro>=0.9.4 and espeak-ng."
        )

    import numpy as np
    import soundfile as sf

    if cancel_check:
        cancel_check()

    safe_rate = max(80, min(450, int(rate)))
    speed = max(0.55, min(1.80, safe_rate / 205.0))
    selected_voice = str(voice or "af_heart").strip() or "af_heart"
    pipeline = _load_kokoro(lang_code)

    chunks = []
    with _MODEL_LOCK:
        for item in pipeline(str(text), voice=selected_voice, speed=speed):
            if cancel_check:
                cancel_check()
            audio = getattr(item, "audio", None)
            if audio is None:
                try:
                    _graphemes, _phonemes, audio = item
                except Exception:
                    audio = None
            if audio is None:
                continue
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            elif hasattr(audio, "numpy"):
                audio = audio.numpy()
            chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1))

    if not chunks:
        raise RuntimeError("Kokoro completed without producing audio.")

    audio = np.concatenate(chunks)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), audio, 24000)
    if cancel_check:
        cancel_check()
    if not out_wav.exists() or out_wav.stat().st_size <= 44:
        raise RuntimeError("Kokoro completed without producing usable audio.")


KOKORO_VOICE_PRESETS = {
    "female": {
        "child": "af_heart",
        "adult": "af_bella",
        "older": "af_sarah",
    },
    "male": {
        "child": "am_adam",
        "adult": "am_adam",
        "older": "am_michael",
    },
    "neutral": {
        "child": "af_heart",
        "adult": "af_heart",
        "older": "am_michael",
    },
}


def automatic_kokoro_voice(profile: dict | None) -> str:
    profile = profile or {}
    voice_class = str(profile.get("voice_class") or "neutral").lower()
    age_group = str(profile.get("age_group") or "adult").lower()
    voices = KOKORO_VOICE_PRESETS.get(voice_class, KOKORO_VOICE_PRESETS["neutral"])
    return voices.get(age_group, voices["adult"])
