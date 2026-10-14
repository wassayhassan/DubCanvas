"""Run real local model requests through the production isolated-worker protocol.

Requests are supplied as a JSON manifest; no cloud calls or inferred human scores.
Model downloads may be large. Failed requests are recorded and other probes continue.
"""
import argparse
import json
import math
from pathlib import Path
import platform
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from anime_dubber.core import CommandRunner, _atomic_json_write
from anime_dubber.quality_models import options
from anime_dubber.quality_runtime import Worker
from anime_dubber.application.diagnostics import DiagnosticLog
from tools.evaluate_quality import diagnostic_metrics

ROLES = {"asr", "verifier", "fallback", "aligner", "translator", "reviewer", "tts", "kokoro", "diarizer", "separator", "index_clone"}

def validate_result(role, result):
    if not isinstance(result, dict):
        raise ValueError("Model result must be an object")
    if role in {"asr", "verifier", "fallback", "translator", "reviewer"}:
        if not str(result.get("text", "")).strip():
            raise ValueError("Model returned no text")
    elif role == "aligner":
        items = result.get("items", [])
        if not items:
            raise ValueError("Aligner returned no spans")
        for item in items:
            a, b = float(item["start_time"]), float(item["end_time"])
            if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
                raise ValueError("Aligner returned invalid spans")
    elif role == "diarizer":
        if not result.get("tracks"):
            raise ValueError("Diarizer returned no tracks")
    elif role in {"tts", "kokoro", "index_clone", "separator"}:
        import numpy as np
        import soundfile as sf
        path = Path(result["output"])
        paths = sorted(path.glob("*.wav")) if role == "separator" else [path]
        if not paths:
            raise ValueError("No audio output")
        for audio in paths:
            with sf.SoundFile(audio) as handle:
                if handle.frames == 0:
                    raise ValueError("Empty audio output")
                for block in handle.blocks(blocksize=16384):
                    if not np.isfinite(block).all():
                        raise ValueError("Non-finite audio output")

def probe(manifest, settings, output, worker_factory=Worker):
    requests = manifest.get("probes")
    if not isinstance(requests, list) or not requests:
        raise ValueError("Provide a nonempty probes list")
    for row in requests:
        if not isinstance(row, dict) or row.get("role") not in ROLES or not isinstance(row.get("request"), dict):
            raise ValueError("Every probe requires a supported role and request object")
    q = options(settings)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {"platform": platform.platform(), "machine": platform.machine(), "probes": [],
              "hardware_performance_verified": False, "quality_verified": False,
              "note": "Successful requests establish execution and output structure, not accuracy or perceptual quality."}
    for i, row in enumerate(requests):
        log = DiagnosticLog(output / "logs", f"probe_{i:03}_{row['role']}_{uuid.uuid4().hex[:12]}", {})
        runner = CommandRunner()
        runner.diagnostic = lambda message, **details: log.write(message, details=details)
        runner.progress = lambda message: log.write(message)
        start = time.monotonic()
        record = {"role": row["role"], "status": "failed", "error": None,
                  "diagnostic_log": str(log.path), "diagnostic_jsonl": str(log.json_path)}
        try:
            with worker_factory(row["role"], q, runner) as worker:
                result = worker.call(row["request"])
                validate_result(row["role"], result)
                record.update(status="completed", result=result)
        except Exception as exc:
            record["error"] = log.redact(str(exc))
            log.write(str(exc), level="error")
        record["wall_seconds"] = time.monotonic() - start
        record["metrics"] = diagnostic_metrics(log.json_path)
        report["probes"].append(record)
        _atomic_json_write(output / "model-probes.json", report)
    return report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        parser.error("Real quality model probes require native Apple Silicon")
    report = probe(json.loads(args.manifest.read_text(encoding="utf-8")),
                   json.loads(args.settings.read_text(encoding="utf-8")), args.output)
    print(args.output / "model-probes.json")
    return 0 if all(x["status"] == "completed" for x in report["probes"]) else 1

if __name__ == "__main__":
    raise SystemExit(main())
