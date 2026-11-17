"""Windowed separation with overlapping context and bounded-memory assembly."""

import hashlib
import json
from dataclasses import replace

import soundfile as sf

from .audio_analysis import fingerprint


def separate_windows(audio, work, runner, progress, config):
    from .core import separate_dialogue, _complete_wav

    info = sf.info(str(audio))
    span = max(30, min(300, config.chunk_seconds))
    identity = hashlib.sha256(
        json.dumps([1, fingerprint(audio), span, config.demucs_device]).encode()
    ).hexdigest()[:20]
    root = work / ("separation_windows_" + identity)
    root.mkdir(parents=True, exist_ok=True)
    destinations = [root / "vocals.wav", root / "no_vocals.wav"]
    if (
        config.resume
        and not config.force
        and all(
            _complete_wav(p, minimum_seconds=info.duration * 0.999)
            for p in destinations
        )
    ):
        return tuple(destinations)
    temporary = [p.with_suffix(".partial.wav") for p in destinations]
    try:
        with (
            sf.SoundFile(str(audio)) as source,
            sf.SoundFile(
                str(temporary[0]),
                "w",
                samplerate=info.samplerate,
                channels=info.channels,
                subtype="PCM_16",
            ) as voices,
            sf.SoundFile(
                str(temporary[1]),
                "w",
                samplerate=info.samplerate,
                channels=info.channels,
                subtype="PCM_16",
            ) as background,
        ):
            for index, start in enumerate(
                range(0, info.frames, span * info.samplerate)
            ):
                runner.check_cancel()
                end = min(info.frames, start + span * info.samplerate)
                context_start = max(0, start - 2 * info.samplerate)
                context_end = min(info.frames, end + 2 * info.samplerate)
                directory = root / f"{index:06d}"
                directory.mkdir(exist_ok=True)
                clip = directory / "input.wav"
                if config.force or not _complete_wav(
                    clip,
                    minimum_seconds=(context_end - context_start)
                    / info.samplerate
                    * 0.999,
                ):
                    source.seek(context_start)
                    sf.write(
                        str(clip),
                        source.read(context_end - context_start, dtype="float32"),
                        info.samplerate,
                    )
                stems = separate_dialogue(
                    clip,
                    directory,
                    runner,
                    progress,
                    replace(config, chunk_seconds=span + 5),
                    windowed=False,
                )
                for path, destination in zip(stems, (voices, background)):
                    with sf.SoundFile(str(path)) as stem:
                        stem.seek(start - context_start)
                        remaining = end - start
                        while remaining:
                            runner.check_cancel()
                            block = stem.read(
                                min(remaining, info.samplerate),
                                dtype="float32",
                                always_2d=True,
                            )
                            if not len(block):
                                raise ValueError("Incomplete separated window")
                            destination.write(block)
                            remaining -= len(block)
                progress(
                    f"Separating soundtrack: {end / info.samplerate:.0f}/{info.duration:.0f}s"
                )
        for pending, destination in zip(temporary, destinations):
            pending.replace(destination)
    finally:
        for pending in temporary:
            pending.unlink(missing_ok=True)
    return tuple(destinations)
