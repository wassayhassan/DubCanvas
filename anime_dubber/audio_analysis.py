"""Bounded-memory vocals activity, timing windows and smooth background blending.

This is a conservative energy VAD on an isolated vocals stem, not a linguistic
speech classifier. Singing and vocal effects are intentionally suppressed too.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf


def fingerprint(path):
    path = Path(path)
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


def activity(path, runner, *, frame_seconds=0.02, threshold_db=-45.0):
    intervals = []
    start = None
    offset = 0
    with sf.SoundFile(str(path)) as source:
        rate = source.samplerate
        frame = max(1, round(rate * frame_seconds))
        while True:
            runner.check_cancel()
            samples = source.read(frame, dtype="float32", always_2d=True)
            if not len(samples):
                break
            rms = float(np.sqrt(np.mean(samples.astype("float64") ** 2)))
            active = rms > 10 ** (threshold_db / 20)
            if active and start is None:
                start = offset / rate
            if not active and start is not None:
                intervals.append((start, offset / rate))
                start = None
            offset += len(samples)
        if start is not None:
            intervals.append((start, offset / rate))
    return merge_intervals(intervals, offset / rate, padding=0.08, gap=0.18)


def merge_intervals(intervals, duration, *, padding=0.0, gap=0.0):
    merged = []
    for a, b in sorted((float(a), float(b)) for a, b in intervals):
        a, b = max(0.0, a - padding), min(duration, b + padding)
        if b <= a:
            continue
        if merged and a <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def analyze_vocals(path, work, runner, *, force=False):
    from .core import _atomic_json_write

    identity = hashlib.sha256(json.dumps([2, fingerprint(path)]).encode()).hexdigest()[
        :20
    ]
    cache = Path(work) / f"vocals_activity_{identity}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text())
    intervals = activity(path, runner)
    _atomic_json_write(cache, intervals)
    return intervals


def snap_segments(segments, intervals, duration, *, tolerance=0.2):
    """Only snap close boundaries. Never move a cue across a neighboring cue."""
    from dataclasses import replace

    result = []
    for i, seg in enumerate(segments):
        onset = min(
            (a for a, b in intervals if b > seg.start and a < seg.end),
            key=lambda a: abs(a - seg.start),
            default=seg.start,
        )
        offset = min(
            (b for a, b in intervals if b > seg.start and a < seg.end),
            key=lambda b: abs(b - seg.end),
            default=seg.end,
        )
        start = onset if abs(onset - seg.start) <= tolerance else seg.start
        end = offset if abs(offset - seg.end) <= tolerance else seg.end
        start = max(
            0,
            start,
            result[-1].end if result and segments[i - 1].end <= seg.start else 0,
        )
        end = min(
            duration,
            end,
            segments[i + 1].start
            if i + 1 < len(segments) and seg.end <= segments[i + 1].start
            else duration,
        )
        result.append(replace(seg, start=start, end=max(start + 0.01, end)))
    return result


def timing_windows(segments, intervals, duration):
    """Partition silence at midpoints; neighbors can never borrow the same gap."""
    windows = []
    for i, seg in enumerate(segments):
        left = segments[i - 1].end if i else 0.0
        right = segments[i + 1].start if i + 1 < len(segments) else duration
        left = (left + seg.start) / 2 if i else 0.0
        right = (seg.end + right) / 2 if i + 1 < len(segments) else duration
        # Untranscribed vocal activity is occupied space, not borrowable silence.
        left = max([left] + [b for a, b in intervals if left < b <= seg.start])
        right = min([right] + [a for a, b in intervals if seg.end <= a < right])
        windows.append(
            (max(0, min(seg.start, left)), min(duration, max(seg.end, right)))
        )
    return windows


def window_rms(path, start, end):
    with sf.SoundFile(str(path)) as source:
        source.seek(min(len(source), max(0, round(start * source.samplerate))))
        remaining = max(0, round((end - start) * source.samplerate))
        total, count = 0.0, 0
        while remaining:
            data = source.read(
                min(remaining, source.samplerate), dtype="float32", always_2d=True
            )
            if not len(data):
                break
            total += float(np.sum(data.astype("float64") ** 2))
            count += data.size
            remaining -= len(data)
    return math.sqrt(total / max(1, count))


def blend_background(
    original, separated, intervals, destination, duration, runner, progress
):
    """One-second blocks; envelopes use absolute time so boundaries do not click."""
    temp = destination.with_suffix(".partial.wav")
    try:
        with sf.SoundFile(str(original)) as source, sf.SoundFile(str(separated)) as bed:
            if (source.samplerate, source.channels) != (bed.samplerate, bed.channels):
                raise ValueError(
                    "Background stems must have matching sample rates and channels"
                )
            rate = source.samplerate
            total = round(duration * rate)
            with sf.SoundFile(
                str(temp),
                "w",
                samplerate=rate,
                channels=source.channels,
                subtype="PCM_16",
            ) as output:
                cursor, first = 0, 0
                while cursor < total:
                    runner.check_cancel()
                    count = min(rate, total - cursor)
                    original_block = source.read(count, dtype="float32", always_2d=True)
                    separated_block = bed.read(count, dtype="float32", always_2d=True)
                    if len(original_block) != count or len(separated_block) != count:
                        raise ValueError("Incomplete background stem")
                    times = (cursor + np.arange(count)) / rate
                    mask = np.zeros(count, dtype="float32")
                    while (
                        first < len(intervals) and intervals[first][1] < times[0] - 0.1
                    ):
                        first += 1
                    for a, b in intervals[first:]:
                        if a > times[-1] + 0.1:
                            break
                        mask = np.maximum(
                            mask,
                            np.minimum(
                                np.clip((times - a + 0.1) / 0.1, 0, 1),
                                np.clip((b + 0.1 - times) / 0.1, 0, 1),
                            ),
                        )
                    output.write(
                        original_block * (1 - mask[:, None])
                        + separated_block * mask[:, None]
                    )
                    cursor += count
                    progress(f"Preparing music and effects: {cursor}/{total}")
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def write_silence(destination, seconds, runner, *, rate=44100):
    """Write an atomic PCM placeholder in bounded blocks, without an encoder."""
    import wave

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".partial.wav")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(rate)
            remaining = max(1, round(seconds * rate))
            block = b"\0" * (rate * 4)
            while remaining:
                runner.check_cancel()
                frames = min(rate, remaining)
                output.writeframes(block[: frames * 4])
                remaining -= frames
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
