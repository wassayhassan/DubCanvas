"""Vocal-boundary ASR checkpoints with cancellable isolated inference."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import soundfile as sf
import psutil

from .audio_analysis import activity, fingerprint


def unreliable(row):
    return (
        float(row.get("avg_logprob") or 0) < -1.0
        or float(row.get("no_speech_prob") or 0) > 0.6
        or float(row.get("compression_ratio") or 0) > 2.4
    )


def transcribe_chunks(audio, config, work, runner, progress, task, fallback=None):
    from .core import _atomic_json_write, validate_transcript_content, Segment
    from .quality_runtime import Worker

    info = sf.info(str(audio))
    intervals = activity(audio, runner)
    boundaries = [0.0]
    limit = max(10, min(120, config.asr_chunk_seconds))
    while boundaries[-1] < info.duration:
        target = min(info.duration, boundaries[-1] + limit)
        # Prefer the end of a nearby vocal region, not the middle of a word.
        cut = max([b for a, b in intervals if target - 3 <= b <= target] or [target])
        if target == info.duration:
            cut = target
        boundaries.append(cut)
    model = (
        config.mlx_whisper_model
        if config.asr_provider != "faster_whisper"
        else config.faster_whisper_model
    )
    identity = hashlib.sha256(
        json.dumps(
            [
                3,
                fingerprint(audio),
                fingerprint(fallback) if fallback else None,
                model,
                config.asr_provider,
                config.source_language,
                task,
                config.asr_prompt,
                config.glossary,
                boundaries,
            ]
        ).encode()
    ).hexdigest()[:24]
    directory = Path(work) / ("asr_chunks_" + identity)
    directory.mkdir(parents=True, exist_ok=True)
    settings = {
        "mlx_python": sys.executable,
        "worker_timeout": 600,
        "worker_memory_gb": 12,
        "system_memory_gb": psutil.virtual_memory().total / 1e9 * 0.9,
        "models": {"legacy_asr": {"repo": model}},
        "asr_provider": config.asr_provider,
        "asr_device": config.faster_whisper_device,
        "asr_compute_type": config.faster_whisper_compute_type,
    }
    language = None if config.source_language == "auto" else config.source_language
    output = []

    def extract(path, start, end, destination):
        with sf.SoundFile(str(path)) as source:
            source.seek(round(start * source.samplerate))
            sf.write(
                str(destination),
                source.read(round((end - start) * source.samplerate), dtype="float32"),
                source.samplerate,
            )

    with Worker("legacy_asr", settings, runner) as worker:

        def recognize(path, start, end):
            clip = directory / "input.wav"
            extract(path, start, end, clip)
            try:
                return worker.call(
                    {
                        "audio": str(clip),
                        "language": language,
                        "task": task,
                        "initial_prompt": config.asr_prompt
                        or " ".join(config.glossary)
                        or None,
                    }
                )
            finally:
                clip.unlink(missing_ok=True)

        for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            runner.check_cancel()
            checkpoint = directory / f"{i:06d}.json"
            result = None
            if config.resume and not config.force and checkpoint.exists():
                try:
                    result = json.loads(checkpoint.read_text())
                except (ValueError, OSError):
                    pass
            if result is None:
                # Overlap only the input. Midpoint ownership prevents duplicated cues.
                offset = max(0, start - 0.3)
                stop = min(info.duration, end + 0.3)
                result = recognize(audio, offset, stop)
                rows = result.get("segments", [])
                repaired = []
                for row in rows:
                    a, b = float(row["start"]), float(row["end"])
                    if fallback and unreliable(row):
                        alternative = recognize(
                            fallback, offset + a, min(stop, offset + b)
                        )
                        candidates = alternative.get("segments", [])
                        if candidates and all(not unreliable(r) for r in candidates):
                            for replacement in candidates:
                                replacement = dict(
                                    replacement,
                                    start=replacement["start"] + a,
                                    end=replacement["end"] + a,
                                )
                                replacement["words"] = [
                                    dict(w, start=w["start"] + a, end=w["end"] + a)
                                    for w in replacement.get("words", [])
                                ]
                                repaired.append(replacement)
                            continue
                    repaired.append(row)
                rows = []
                for row in repaired:
                    if not start <= offset + (row["start"] + row["end"]) / 2 < end:
                        continue
                    row = dict(
                        row,
                        start=max(start, row["start"] + offset),
                        end=min(end, row["end"] + offset),
                    )
                    row["words"] = [
                        dict(
                            w,
                            start=max(start, w["start"] + offset),
                            end=min(end, w["end"] + offset),
                        )
                        for w in row.get("words", [])
                    ]
                    rows.append(row)
                validate_transcript_content(
                    [Segment(r["start"], r["end"], r.get("text", "")) for r in rows]
                )
                result = {"language": result.get("language"), "segments": rows}
                _atomic_json_write(checkpoint, result)
            if language is None and result.get("segments"):
                language = result.get("language")
            output.extend(result["segments"])
            progress(f"Transcribing: {i + 1}/{len(boundaries) - 1} chunks")
    return {"language": language, "segments": output}
