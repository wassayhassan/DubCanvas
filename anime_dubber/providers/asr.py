from __future__ import annotations

import importlib.util
import platform
from pathlib import Path
from typing import Any, Callable, Dict, List


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def resolve_asr_provider(requested: str = "auto") -> str:
    value = (requested or "auto").strip().lower().replace("-", "_")
    aliases = {
        "mlx": "mlx_whisper",
        "mlxwhisper": "mlx_whisper",
        "faster": "faster_whisper",
        "fasterwhisper": "faster_whisper",
    }
    value = aliases.get(value, value)
    if value != "auto":
        return value

    if platform.system() == "Darwin" and platform.machine() == "arm64" and _has_module("mlx_whisper"):
        return "mlx_whisper"
    if _has_module("faster_whisper"):
        return "faster_whisper"
    return "mlx_whisper" if platform.system() == "Darwin" else "faster_whisper"


def faster_whisper_segments(
    audio: Path,
    *,
    task: str,
    model_name: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
    language: str | None = "zh",
    language_sink: Callable[[str], None] | None = None,
    initial_prompt: str | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> List[Dict[str, Any]]:
    """Run Faster-Whisper and return provider-neutral segment dictionaries."""
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Install the cross-platform requirements "
            "or choose the MLX Whisper provider on Apple silicon."
        ) from exc

    model = WhisperModel(
        model_name,
        device=device or "auto",
        compute_type=compute_type or "auto",
    )
    iterator, info = model.transcribe(
        str(audio),
        language=language,
        task=task,
        initial_prompt=initial_prompt,
        word_timestamps=True,
        vad_filter=True,
    )
    if language_sink and getattr(info, "language", None):
        language_sink(str(info.language).lower())

    rows: List[Dict[str, Any]] = []
    for seg in iterator:
        if cancel_check:
            cancel_check()
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        words = []
        for word in getattr(seg, "words", None) or []:
            words.append({
                "start": float(getattr(word, "start", 0.0) or 0.0),
                "end": float(getattr(word, "end", 0.0) or 0.0),
                "word": str(getattr(word, "word", "") or ""),
            })
        rows.append({
            "start": float(getattr(seg, "start", 0.0) or 0.0),
            "end": float(getattr(seg, "end", 0.0) or 0.0),
            "text": text,
            "words": words,
            "avg_logprob": getattr(seg, "avg_logprob", None),
            "no_speech_prob": getattr(seg, "no_speech_prob", None),
            "compression_ratio": getattr(seg, "compression_ratio", None),
        })
    return rows
