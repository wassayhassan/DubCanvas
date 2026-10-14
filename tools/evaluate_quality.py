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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    q = json.loads(args.settings.read_text())
    manifest = json.loads(args.manifest.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    service = ApplicationService()
    result = {"platform": platform.platform(), "hardware_performance_verified": False, "clips": []}
    for i, clip in enumerate(manifest["clips"]):
        start = time.monotonic()
        job = service.run_sync({"source": clip["source"], "output_dir": str(args.output / f"clip_{i:03}"),
            "pipeline_profile": "quality_v1", "quality_settings": q, "source_language": "zh", "target_language": "en"})
        row = {"source": clip["source"], "status": job["status"], "error": job.get("error"),
            "wall_seconds": time.monotonic() - start, "artifacts": job.get("result", {}),
            "human_scores": clip.get("human_scores", {}), "cer": None, "missing_expected_terms": None}
        if job["status"] == "completed":
            artifacts = job["result"]
            if clip.get("gold_source_srt"):
                gold = "".join(cue["text"] for cue in read_srt(Path(clip["gold_source_srt"])))
                recognized = "".join(cue["text"] for cue in read_srt(Path(artifacts["chinese_srt"])))
                row["cer"] = edit_distance(gold, recognized) / max(1, len(gold))
            translated = Path(artifacts["translated_srt"]).read_text()
            row["missing_expected_terms"] = [term for term in clip.get("expected_terms", []) if term.casefold() not in translated.casefold()]
        result["clips"].append(row)
        _atomic_json_write(args.output / "evaluation.json", result)
    print(args.output / "evaluation.json")
    return 0 if all(x["status"] == "completed" for x in result["clips"]) else 1

if __name__ == "__main__": raise SystemExit(main())
