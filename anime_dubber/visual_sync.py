"""Conservative visual onset correction for already generated dub clips.

This does not synthesize mouth shapes. It moves only a line whose single,
stable on-screen face begins speaking close to the source transcript boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
import wave
from array import array
from pathlib import Path
from typing import Callable, Optional, Sequence

from .core import Segment, _atomic_json_write, ffprobe_duration

MAX_SHIFT = 0.18
SAMPLE_RATE = 12.0


def _audible_onset(clip: Path) -> float:
    """Measure a residual lead-in in the processed PCM voice clip."""
    try:
        with wave.open(str(clip), "rb") as wav:
            if wav.getsampwidth() != 2 or wav.getframerate() <= 0:
                return 0.0
            rate = wav.getframerate()
            frames = min(wav.getnframes(), int(rate * .25))
            raw = array("h")
            raw.frombytes(wav.readframes(frames))
            channels = wav.getnchannels()
            block = max(1, int(rate * .01)) * channels
            rms = [math.sqrt(sum(v * v for v in raw[i:i + block]) / block)
                   for i in range(0, len(raw) - block + 1, block)]
            if len(rms) < 3:
                return 0.0
            threshold = max(120.0, max(rms) * .12)
            for i in range(len(rms) - 1):
                if rms[i] >= threshold and rms[i + 1] >= threshold:
                    return i * block / channels / rate
    except (OSError, EOFError, wave.Error, ValueError):
        pass
    return 0.0


def _visual_onset(times: Sequence[float], motion: Sequence[float], boundary: float) -> Optional[float]:
    """Find a sustained mouth-motion onset near a source speech boundary."""
    if len(times) != len(motion) or len(times) < 6:
        return None
    before = [m for t, m in zip(times, motion) if t < boundary - 0.12]
    if not before or not all(math.isfinite(m) and m >= 0 for m in motion):
        return None
    baseline = sorted(before)[len(before) // 2]
    threshold = max(4.0, baseline * 2.2)
    if max(motion) < threshold * 1.4 or motion[0] >= threshold:
        return None
    for i in range(1, len(motion) - 2):
        if motion[i] >= threshold and sum(m >= threshold for m in motion[i:i + 3]) >= 2:
            onset = times[i]
            if abs(onset - boundary) <= MAX_SHIFT and min(motion[:i]) < threshold * .6:
                return onset
            return None
    return None


def _mouth_motion(video: Path, boundary: float, capture, detector, cv2) -> Optional[tuple[list[float], list[float]]]:
    import numpy as np

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    if not math.isfinite(fps) or fps < 4:
        return None
    step = max(1 / SAMPLE_RATE, 1 / fps)
    start = max(0.0, boundary - .34)
    times, motion = [], []
    previous = None
    anchor_box = None
    if not capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000):
        return None
    next_sample = start
    for frame_index in range(int(fps * 1.5) + 2):
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        actual = float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0) / 1000
        t = actual if math.isfinite(actual) and actual > 0 else start + frame_index / fps
        if t < next_sample - .5 / fps:
            continue
        if t > boundary + .35:
            break
        next_sample += step
        h, w = frame.shape[:2]
        if h < 80 or w < 80:
            return None
        gray = cv2.cvtColor(cv2.resize(frame, (int(w * min(1, 480 / w)),
                                                int(h * min(1, 480 / w)))), cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(48, 48))
        if len(faces) != 1:
            return None
        x, y, fw, fh = [int(v) for v in faces[0]]
        box = (x, y, fw, fh)
        if anchor_box is None:
            anchor_box = box
        else:
            px, py, pw, ph = anchor_box
            if (abs(x + fw / 2 - px - pw / 2) > pw * .12 or
                    abs(y + fh / 2 - py - ph / 2) > ph * .12 or
                    abs(fw - pw) > pw * .15 or abs(fh - ph) > ph * .15):
                return None  # Cut, camera movement, or another character.
        x, y, fw, fh = anchor_box
        def crop(y0: float, y1: float):
            region = gray[y + int(y0 * fh):y + int(y1 * fh),
                          x + int(.23 * fw):x + int(.77 * fw)]
            return cv2.resize(region, (40, 20)).astype(np.float32)
        lower, upper = crop(.66, .90), crop(.27, .50)
        if previous is not None:
            old_lower, old_upper = previous
            mouth = float(np.mean(np.abs(lower - old_lower)))
            reference = float(np.mean(np.abs(upper - old_upper)))
            times.append(t)
            motion.append(max(0.0, mouth - reference * 1.5))
        previous = (lower, upper)
    return (times, motion) if len(times) >= 6 else None


def align_dub_to_visible_speech(
    video: Path, segments: Sequence[Segment], clips: Sequence[Path],
    work_dir: Path, runner, progress: Callable[[str], None],
    *, total_duration: Optional[float] = None, resume: bool = True, force: bool = False,
) -> list[Segment]:
    """Return separate timeline cues; subtitle and source cue timings stay intact."""
    aligned = [Segment.from_dict(s.to_dict()) for s in segments]
    if len(aligned) != len(clips) or not aligned:
        return aligned
    try:
        import cv2
        detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
        if detector.empty():
            raise RuntimeError("Face detector is unavailable")
    except Exception as exc:
        progress(f"Visual timing unavailable ({exc}); keeping existing dub timing")
        return aligned

    signature = hashlib.sha1(json.dumps({
        "version": 1,
        "duration": total_duration,
        "video": [str(video.resolve()), video.stat().st_size, video.stat().st_mtime_ns],
        "segments": [[round(s.start, 3), round(s.end, 3), s.speaker_id] for s in segments],
        "clips": [[str(p), p.stat().st_size, p.stat().st_mtime_ns] for p in clips],
    }, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    report = work_dir / f"visual_timing_{signature}.json"
    if resume and not force and report.exists():
        try:
            decisions = json.loads(report.read_text(encoding="utf-8"))
            offsets = decisions["offsets"]
            if len(offsets) == len(aligned) and all(isinstance(x, (int, float)) and
                                                      math.isfinite(x) and abs(x) <= MAX_SHIFT for x in offsets):
                for cue, offset in zip(aligned, offsets):
                    cue.start += offset
                    cue.end += offset
                progress(f"Reusing visual timing for {sum(abs(x) > .001 for x in offsets)} lines")
                return aligned
        except (OSError, ValueError, KeyError, TypeError):
            pass

    try:
        capture = cv2.VideoCapture(str(video))
    except Exception as exc:
        progress(f"Visual timing cannot open video frames ({exc}); keeping existing dub timing")
        return aligned
    if not capture.isOpened():
        progress("Visual timing cannot read video frames; keeping existing dub timing")
        capture.release()
        return aligned
    offsets, reasons = [], []
    try:
        durations = [ffprobe_duration(clip, runner) for clip in clips]
        for i, (seg, clip, duration) in enumerate(zip(segments, clips, durations)):
            runner.check_cancel()
            shift, reason = 0.0, "no confident on-screen speaker"
            if seg.start >= .35 and seg.end - seg.start >= .45:
                try:
                    samples = _mouth_motion(video, seg.start, capture, detector, cv2)
                except Exception:
                    # Decode and detector failures must never discard a
                    # completed dub or its source-aligned timing.
                    samples = None
                onset = _visual_onset(*samples, seg.start) if samples else None
                if onset is not None:
                    candidate = round(onset - seg.start - _audible_onset(clip), 3)
                    next_start = (segments[i + 1].start if i + 1 < len(segments)
                                  else total_duration if total_duration is not None else float("inf"))
                    previous_end = (aligned[i - 1].start + durations[i - 1] if i else 0.0)
                    if (abs(candidate) >= .04 and seg.start + candidate >= previous_end - .02
                            and seg.start + candidate + duration <= next_start + .02):
                        shift, reason = candidate, "single stable face and mouth onset"
                    elif abs(candidate) < .04:
                        reason = "already aligned"
                    else:
                        reason = "shift would overlap another voice"
            offsets.append(shift)
            reasons.append(reason)
            aligned[i].start += shift
            aligned[i].end += shift
            if (i + 1) % 50 == 0:
                progress(f"Checking visible mouth timing: {i + 1}/{len(segments)} lines")
    finally:
        capture.release()
    _atomic_json_write(report, {"signature": signature, "offsets": offsets, "reasons": reasons})
    progress(f"Visual timing adjusted {sum(abs(x) > .001 for x in offsets)}/{len(segments)} lines; "
             f"uncertain shots kept their original timing")
    return aligned
