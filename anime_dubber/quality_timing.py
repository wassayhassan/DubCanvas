"""Hard safety constraints and joint timeline validation for quality dubbing."""
from __future__ import annotations
import re

def window(segments, index, duration, cuts, settings):
    seg = segments[index]
    offscreen = index + 1 in settings["off_screen_cues"]
    end = min(duration, seg.end + settings["off_screen_borrow" if offscreen else "on_screen_borrow"])
    # Original overlaps get independent tracks, but invented overlap is forbidden.
    for other in segments[index + 1:]:
        if other.start >= seg.end - .02:
            end = min(end, other.start - settings["speaker_guard"])
            break
        if other.start > seg.start and other.speaker_id == seg.speaker_id:
            end = min(end, other.start - settings["speaker_guard"])
            break
    for cut in cuts:
        if seg.start < cut and cut >= seg.end - .05:
            end = min(end, cut)
            break
    return max(.001, end - seg.start)

def rate_limit(style, settings, rescue=False):
    if style in {"shouting", "whispering"}:
        return min(settings["maximum_speed"], settings["expressive_speed"])
    if rescue and settings["allow_rescue_speed"]:
        return settings["maximum_speed"]
    return min(settings["maximum_speed"], settings["urgent_speed"] if style == "urgent" else settings["normal_speed"])

def choose_tempo(duration, available, style, settings, native_acceleration=1, rescue=False):
    if available <= 0 or native_acceleration < 1:
        return None
    required = max(1.0, duration / available)
    if required * native_acceleration > 1.5 + 1e-8:
        return None
    return required if required <= rate_limit(style, settings, rescue) + 1e-8 else None

def words(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.casefold())

def edit_distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = current
    return row[-1]

def speech_error(expected, recognized):
    a, b = words(expected), words(recognized)
    return edit_distance(a, b) / max(1, len(a))

def protected_speech_matches(expected, recognized):
    """Small WER must not hide a changed number or dropped negation."""
    a, b = words(expected), words(recognized)
    critical = lambda xs: [x for x in xs if x.isdigit() or x in {"not", "never", "no", "without"} or x.endswith("n't")]
    return critical(a) == critical(b)

def validate_schedule(segments, durations, cuts):
    from .core import PipelineError
    for i, (seg, duration) in enumerate(zip(segments, durations)):
        end = seg.start + duration
        if any(seg.end <= cut + .05 and seg.start < cut < end - .015 for cut in cuts):
            raise PipelineError(f"Generated cue {i + 1} crosses a protected scene cut")
        for j in range(i + 1, len(segments)):
            other = segments[j]
            if other.start >= end - .015:
                break
            original_overlap = other.start < seg.end - .02 and other.speaker_id != seg.speaker_id
            if not original_overlap:
                raise PipelineError(f"Generated cues {i + 1} and {j + 1} collide; render blocked")
