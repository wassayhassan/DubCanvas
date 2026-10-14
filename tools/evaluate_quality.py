"""Real-model evaluation harness. Human scores are input, never fabricated.

Manifest: {"clips":[{"source":"...", "gold_source_srt":"...", "expected_terms":["..."],
"human_scores": {"meaning": null, "voice": null, "background": null}}]}.
"""
import argparse
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from anime_dubber.application.service import ApplicationService
from anime_dubber.quality_timing import edit_distance
from anime_dubber.review import read_srt
from anime_dubber.core import _atomic_json_write

def diagnostic_metrics(path):
    """Aggregate measured logs; RSS and allocator counters are not physical footprint."""
    result = {"stage_seconds": {}, "peak_worker_rss_bytes": None,
              "peak_system_used_bytes": None, "peak_system_swap_bytes": None,
              "swap_growth_bytes": None, "runtime_metadata": [], "log_errors": []}
    result["peak_mlx_allocator_bytes"] = None
    first_swap = None
    if not path or not Path(path).is_file():
        return result
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
                details = event.get("details", {})
                if event.get("message") == "Stage finished":
                    stage = event["stage"]
                    result["stage_seconds"][stage] = result["stage_seconds"].get(stage, 0) + details["duration_seconds"]
                if event.get("message") == "Job finished":
                    # The final stage has no subsequent change_stage call.
                    stage = event["stage"]
                    if details.get("stage_duration_seconds") is not None:
                        result["stage_seconds"][stage] = result["stage_seconds"].get(stage, 0) + details["stage_duration_seconds"]
                if event.get("message") == "Memory observation":
                    for key, source in (("peak_worker_rss_bytes", "rss_bytes"),
                                        ("peak_system_used_bytes", "system_used_bytes"),
                                        ("peak_system_swap_bytes", "system_swap_bytes")):
                        value = details.get(source)
                        if value is not None:
                            result[key] = max(result[key] or 0, value)
                    if first_swap is None:
                        first_swap = details.get("system_swap_bytes")
                if event.get("message") == "Model worker runtime":
                    result["runtime_metadata"].append(details)
                    peak = details.get("mlx_allocator_bytes", {}).get("get_peak_memory")
                    if peak is not None:
                        result["peak_mlx_allocator_bytes"] = max(result["peak_mlx_allocator_bytes"] or 0, peak)
            except (ValueError, KeyError, TypeError) as exc:
                result["log_errors"].append({"line": number, "error": str(exc)})
    if first_swap is not None:
        result["swap_growth_bytes"] = max(0, (result["peak_system_swap_bytes"] or 0) - first_swap)
    return result

def evaluate(manifest, settings, output, service_factory=ApplicationService):
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        raise ValueError("Evaluation requires a nonempty clips list")
    if any(not isinstance(clip, dict) or not clip.get("source") for clip in clips):
        raise ValueError("Every clip requires a source")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    result = {"platform": platform.platform(), "machine": platform.machine(),
              "hardware_performance_verified": False, "physical_footprint_verified": False,
              "quality_verified": False, "clips": []}
    for i, clip in enumerate(clips):
        artifacts = {}
        def collect(event):
            if event.event == "artifact":
                artifacts[event.data["kind"]] = event.data["path"]
        service = service_factory(event_sink=collect)
        start = time.monotonic()
        row = {"source": clip["source"], "status": "failed", "error": None,
               "artifacts": artifacts, "human_scores": clip.get("human_scores", {}),
               "cer": None, "missing_expected_terms": None}
        try:
            job = service.run_sync({"source": clip["source"], "output_dir": str(output / f"clip_{i:03}"),
                "pipeline_profile": "quality_v1", "quality_settings": settings,
                "source_language": "zh", "target_language": "en", "series_id": clip.get("series_id", "")})
            row.update(status=job["status"], error=job.get("error"))
            artifacts.update(job.get("result", {}))
        except Exception as exc:
            # run_sync raises on failure. Persist partial artifact events and
            # continue independent clips instead of losing the whole benchmark.
            row["error"] = str(exc)
        row["wall_seconds"] = time.monotonic() - start
        row["metrics"] = diagnostic_metrics(artifacts.get("diagnostic_jsonl"))
        try:
            if clip.get("gold_source_srt") and artifacts.get("chinese_srt"):
                gold = "".join(cue["text"] for cue in read_srt(Path(clip["gold_source_srt"])))
                recognized = "".join(cue["text"] for cue in read_srt(Path(artifacts["chinese_srt"])))
                row["cer"] = edit_distance(gold, recognized) / max(1, len(gold))
            if artifacts.get("translated_srt"):
                translated = Path(artifacts["translated_srt"]).read_text(encoding="utf-8")
                row["missing_expected_terms"] = [term for term in clip.get("expected_terms", []) if term.casefold() not in translated.casefold()]
        except (OSError, ValueError, KeyError) as exc:
            row["scoring_error"] = str(exc)
        result["clips"].append(row)
        _atomic_json_write(output / "evaluation.json", result)
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    q = json.loads(args.settings.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = evaluate(manifest, q, args.output)
    print(args.output / "evaluation.json")
    return 0 if all(x["status"] == "completed" for x in result["clips"]) else 1

if __name__ == "__main__": raise SystemExit(main())
