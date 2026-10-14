"""Stream-copy export with a checked, unchanged source video timeline."""
import hashlib
import json
import math
from pathlib import Path

from .core import PipelineError, CancelledError, validate_media_streams

def source_duration(video, runner):
    data = json.loads(runner.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration:format=duration",
                                  "-of", "json", str(video)]).stdout)
    if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        raise PipelineError("Source has no video stream")
    values = [data.get("format", {}).get("duration")] + [s.get("duration") for s in data.get("streams", [])]
    times = []
    for value in values:
        try:
            duration = float(value)
            if math.isfinite(duration) and duration > 0:
                times.append(duration)
        except (ValueError, TypeError):
            pass
    if not times:
        raise PipelineError("Cannot determine the complete source timeline")
    return max(times)

def video_packets(video, runner):
    data = json.loads(runner.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets",
        "-show_data_hash", "sha256", "-show_entries", "packet=pts_time,dts_time,duration_time,data_hash:stream=time_base",
        "-of", "json", str(video)]).stdout)
    rows = data.get("packets", [])
    if not rows or any(not row.get("data_hash") for row in rows):
        raise PipelineError("Cannot verify encoded source video packets")
    return rows

def verify_video_copy(source, output, runner):
    before, after = video_packets(source, runner), video_packets(output, runner)
    if len(before) != len(after):
        raise PipelineError("Export changed the video packet count; output not marked complete")
    def number(row, key):
        value = row.get(key)
        return float(value) if value is not None else None
    if any(row.get("pts_time") is None for row in before + after):
        raise PipelineError("Export has unverifiable presentation timing")
    offsets = {}
    for a, b in zip(before, after):
        if a["data_hash"] != b["data_hash"]:
            raise PipelineError("Export changed encoded video content; output not marked complete")
        for key in ("pts_time", "dts_time", "duration_time"):
            x, y = number(a, key), number(b, key)
            if x is None and y is None: continue
            # Some containers omit DTS. PTS is the presentation timeline;
            # compare every available DTS without fabricating absent values.
            if key == "dts_time" and (x is None or y is None): continue
            if x is None or y is None or not math.isfinite(x) or not math.isfinite(y):
                raise PipelineError("Export has unverifiable video timing")
            delta = 0 if key == "duration_time" else offsets.setdefault(key, y - x)
            if abs(y - x - delta) > .00101:
                raise PipelineError("Export changed video pacing; output not marked complete")
    return {"packet_count": len(before), "encoded_sha256": hashlib.sha256(
        "\n".join(row["data_hash"] for row in before).encode()).hexdigest(), "unchanged": True}

def mux_preserving_video(video, audio, final, runner, progress):
    final = Path(final)
    runner.set_stage("exporting", "Exporting original video with dubbed audio")
    # Never use -shortest, a video filter or a video encoder here. MP4 may not
    # support the source codec; a Matroska container preserves it without loss.
    for destination in (final, final.with_suffix(".mkv")):
        temp = destination.with_name(destination.stem + ".partial" + destination.suffix)
        temp.unlink(missing_ok=True)
        try:
            command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio),
                       "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy"]
            if destination.suffix == ".mp4": command += ["-movflags", "+faststart"]
            reply = runner.run(command + [str(temp)], check=False)
            if reply.returncode:
                progress("Source codec needs a different container; preserving video in MKV")
                continue
            try:
                validate_media_streams(temp, runner)
                report = verify_video_copy(video, temp, runner)
            except CancelledError:
                raise
            except PipelineError:
                if destination.suffix == ".mp4":
                    progress("MP4 could not preserve video exactly; automatically trying MKV")
                    continue
                raise
            temp.replace(destination)
            runner.diagnostic("Original video verified", **report)
            return destination
        finally:
            temp.unlink(missing_ok=True)
    raise PipelineError("Cannot stream-copy the source video into a supported container; source and checkpoints preserved")
