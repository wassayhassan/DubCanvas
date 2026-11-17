"""Preserve source tracks and chapters while adding a default dub and soft subs."""

from pathlib import Path
import json


def export_video(
    video,
    audio,
    final,
    runner,
    progress,
    *,
    subtitles=None,
    source_language="und",
    target_language="en",
    allow_reencode=True,
):
    from .core import PipelineError, CancelledError, validate_media_streams
    from .quality_export import verify_video_copy

    subtitle_index = 0
    if subtitles:
        probe = runner.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=index",
                "-of",
                "json",
                str(video),
            ]
        )
        subtitle_index = len(json.loads(probe.stdout).get("streams", []))
    final = Path(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    for destination, reencode in [
        (final, False),
        (final.with_suffix(".mkv"), False),
    ] + ([(final, True)] if allow_reencode else []):
        temp = destination.with_name(destination.stem + ".partial" + destination.suffix)
        temp.unlink(missing_ok=True)
        command = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(audio),
        ]
        if subtitles:
            command += ["-i", str(subtitles)]
        command += [
            "-map",
            "0:v?",
            "-map",
            "1:a:0",
            "-map",
            "0:a?",
            "-map",
            "0:s?",
            "-map",
            "0:t?",
            "-map",
            "0:d?",
            "-map_metadata",
            "0",
            "-map_chapters",
            "0",
            "-c",
            "copy",
            "-disposition:a",
            "0",
            "-disposition:a:0",
            "default",
            "-metadata:s:a:0",
            f"language={language_tag(target_language)}",
            "-metadata:s:a:0",
            "title=DubCanvas dub",
            "-metadata:s:a:1",
            f"language={language_tag(source_language)}",
            "-metadata:s:a:1",
            "title=Original audio",
        ]
        if subtitles:
            command += [
                "-map",
                "2:s:0",
                f"-metadata:s:s:{subtitle_index}",
                f"language={language_tag(target_language)}",
                f"-metadata:s:s:{subtitle_index}",
                "title=DubCanvas subtitles",
            ]
        if destination.suffix == ".mp4":
            command += ["-movflags", "+faststart", "-c:s", "mov_text"]
        if reencode:
            command += [
                "-c:v:0",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "medium",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        try:
            reply = runner.run(command + [str(temp)], check=False)
            if reply.returncode:
                continue
            validate_media_streams(temp, runner)
            if not reencode:
                verify_video_copy(video, temp, runner)
            temp.replace(destination)
            return destination
        except CancelledError:
            raise
        except PipelineError:
            if reencode:
                raise
        finally:
            temp.unlink(missing_ok=True)
        progress("Trying a container that preserves all source tracks")
    raise PipelineError(
        "Could not export all source tracks; source and checkpoints retained"
    )


def language_tag(value):
    return {
        "en": "eng",
        "zh": "zho",
        "ja": "jpn",
        "ko": "kor",
        "es": "spa",
        "fr": "fra",
        "de": "deu",
        "it": "ita",
        "pt": "por",
        "hi": "hin",
        "ar": "ara",
    }.get(value, value if len(value) == 3 else "und")
