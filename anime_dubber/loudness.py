"""Match final integrated loudness to the original, without loading audio in RAM."""

import json
import math


def measure(path, runner, target=-24):
    reply = runner.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"loudnorm=I={target}:TP=-1:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    text = (reply.stderr or "") + (reply.stdout or "")
    try:
        return json.loads(text[text.rfind("{") : text.rfind("}") + 1])
    except ValueError:
        return {}


def match_loudness(mixed, original, runner, progress):
    from .core import PipelineError

    source = measure(original, runner)
    target = float(source.get("input_i", "nan"))
    filt = None
    if math.isfinite(target):
        target = max(-70, min(-5, target))
        stats = measure(mixed, runner, target)
        if float(stats.get("input_i", "nan")) == -math.inf:
            progress(
                "Warning: mixed track is silent; timing review may contain omitted voices"
            )
        elif all(
            math.isfinite(float(stats.get(key, "nan")))
            for key in (
                "input_i",
                "input_tp",
                "input_lra",
                "input_thresh",
                "target_offset",
            )
        ):
            filt = (
                f"loudnorm=I={target}:TP=-1:LRA=11:measured_I={stats['input_i']}:"
                f"measured_TP={stats['input_tp']}:measured_LRA={stats['input_lra']}:"
                f"measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true"
            )
        else:
            raise PipelineError("Cannot measure dub loudness")
    else:
        progress(
            "Original audio has no measurable integrated loudness; preserving mixed level"
        )
    normalized = mixed.with_name(mixed.stem + "_matched.m4a")
    temp = normalized.with_suffix(".partial.m4a")
    try:
        runner.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mixed),
                *(["-af", filt] if filt else []),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(temp),
            ]
        )
        temp.replace(normalized)
    finally:
        temp.unlink(missing_ok=True)
    return normalized
