"""Hard safety constraints and joint timeline validation for quality dubbing."""
from __future__ import annotations
import re
import math

def validate_source_alignment(source, items, duration):
    """Alignment positions may not replace or truncate accepted transcription."""
    normalize = lambda text: re.sub(r"[\W_]", "", normalize_chinese_numbers(text)).casefold()
    if not items or normalize("".join(str(row.get("text", "")) for row in items)) != normalize(source):
        raise ValueError("alignment omitted or added accepted source words")
    previous_start = previous_end = -1.0
    for row in items:
        start, end = float(row["start_time"]), float(row["end_time"])
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start
                or end > duration + .025 or start < previous_start or end < previous_end):
            raise ValueError("alignment has invalid, unordered or out-of-range spans")
        previous_start, previous_end = start, end

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
    a, b = speech_tokens(expected), speech_tokens(recognized)
    return edit_distance(a, b) / max(1, len(a))

def protected_speech_matches(expected, recognized):
    """Small WER must not hide a changed number or dropped negation."""
    a, b = speech_tokens(expected), speech_tokens(recognized)
    critical = lambda xs: [x for x in xs if re.fullmatch(r"\d+(?:\.\d+)?", x) or x in {"not", "never", "no", "without"} or x.endswith("n't")]
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

def chinese_critical_matches(a, b, glossary):
    """Low aggregate CER cannot excuse a changed negation, number or known entity."""
    negation = lambda text: [x for x in text if x in "不没未无非别勿莫"]
    numbers = lambda text: re.findall(r"\d+(?:\.\d+)?", normalize_chinese_numbers(text))
    return (negation(a) == negation(b) and numbers(a) == numbers(b)
            and all((term in a) == (term in b) for term in glossary))

def normalize_chinese_numbers(text):
    digits = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
    def convert(match):
        token = match.group()
        if not any(c in units for c in token):
            return "".join(str(digits[c]) for c in token)
        total = section = number = 0
        for char in token:
            if char in digits:
                number = digits[char]
            elif units[char] < 10000:
                section += (number or 1) * units[char]
                number = 0
            else:
                section += number
                if char == "亿":
                    total = (total + section or 1) * units[char]
                else:
                    total += (section or 1) * units[char]
                section = number = 0
        return str(total + section + number)
    normalized = re.sub(r"[零〇一二两三四五六七八九十百千万亿]+", convert, text)
    normalized = re.sub(r"(?<=\d)点(?=\d)", ".", normalized)
    return re.sub(r"(?<=\d)[,，](?=\d{3}(?:\D|$))", "", normalized)

def speech_tokens(text):
    """Normalize spoken/written English numbers without losing critical quantities."""
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text.casefold())
    raw = re.findall(r"\d+(?:\.\d+)?(?:st|nd|rd|th)?|[a-z]+(?:'[a-z]+)?", text)
    small = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
    tens = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
    ordinal = dict(zip("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split(), range(1, 21)))
    scale = {"hundred":100, "thousand":1000, "million":1000000, "billion":1000000000, "trillion":1000000000000}
    ordinal_scale = {"hundredth":100, "thousandth":1000, "millionth":1000000, "billionth":1000000000, "trillionth":1000000000000}
    output = []
    i = 0
    while i < len(raw):
        token = raw[i]
        if token not in small and token not in tens and token not in ordinal and token not in scale and token not in ordinal_scale:
            output.append(re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", token))
            i += 1
            continue
        start = i
        current = total = 0
        used_scale = False
        while i < len(raw):
            token = raw[i]
            if token == "and" and used_scale and i + 1 < len(raw) and (raw[i + 1] in small or raw[i + 1] in tens or raw[i + 1] in ordinal):
                i += 1
                continue
            if token in scale or token in ordinal_scale:
                unit = scale.get(token, ordinal_scale.get(token))
                if unit == 100:
                    current = (current or 1) * unit
                else:
                    total += (current or 1) * unit
                    current = 0
                used_scale = True
                i += 1
                if token in ordinal_scale:
                    break
                continue
            value = small.get(token, tens.get(token, ordinal.get(token)))
            if value is None:
                break
            if i > start and not used_scale and not (current >= 20 and current % 10 == 0 and value < 10):
                break
            current += value
            i += 1
            if token in ordinal:
                break
        output.append(str(total + current))
    normalized = []
    i = 0
    while i < len(output):
        if output[i].isdigit() and i + 2 < len(output) and output[i + 1] == "point":
            end = i + 2
            fraction = []
            while end < len(output) and output[end].isdigit() and len(output[end]) == 1:
                fraction.append(output[end]); end += 1
            if fraction:
                normalized.append(output[i] + "." + "".join(fraction)); i = end; continue
        normalized.append(output[i]); i += 1
    return normalized
