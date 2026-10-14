import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from anime_dubber.application.events import AppEvent
from anime_dubber.core import CommandRunner, PipelineError
from anime_dubber.quality_models import options
from anime_dubber.quality_models import verify_translation_adapter, file_digest
from anime_dubber.quality_runtime import Worker
from tools.evaluate_quality import evaluate, diagnostic_metrics
from tools.probe_quality_models import probe, validate_result
from tools.install_quality import checked_step


class QualityEvaluationTests(unittest.TestCase):
    def test_adapter_checksum_cannot_authorize_another_configured_code_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "hy_v3.py").write_text("# audited code")
            checksum = file_digest(root / "hy_v3.py")
            (root / "config.json").write_text(json.dumps({"model_file": "other.py"}))
            with self.assertRaisesRegex(ValueError, "exactly"):
                verify_translation_adapter(root, checksum)
            (root / "config.json").write_text(json.dumps({"model_file": "hy_v3.py"}))
            self.assertEqual(verify_translation_adapter(root, checksum), root.resolve())
    def test_failed_clip_keeps_artifacts_and_other_clips_run(self):
        class Service:
            def __init__(self, event_sink): self.sink = event_sink
            def run_sync(self, config):
                self.sink(AppEvent("artifact", {"kind": "partial_translated_srt", "path": "saved.srt"}))
                if config["source"] == "failed": raise RuntimeError("meaning review failed")
                return {"status": "completed", "result": {}}
        with tempfile.TemporaryDirectory() as td:
            result = evaluate({"clips": [{"source": "failed"}, {"source": "okay"}]}, {}, td, Service)
            self.assertEqual([r["status"] for r in result["clips"]], ["failed", "completed"])
            self.assertEqual(result["clips"][0]["artifacts"]["partial_translated_srt"], "saved.srt")
            self.assertFalse(result["quality_verified"])
            self.assertEqual(json.loads((Path(td) / "evaluation.json").read_text()), result)

    def test_empty_corpus_cannot_pass(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ValueError):
            evaluate({"clips": []}, {}, td)

    def test_stage_metrics_separate_rss_allocator_swap(self):
        rows = [
            {"message": "Stage finished", "stage": "asr", "details": {"duration_seconds": 2}},
            {"message": "Stage finished", "stage": "asr", "details": {"duration_seconds": 3}},
            {"message": "Job finished", "stage": "mix", "details": {"stage_duration_seconds": 4}},
            {"message": "Memory observation", "details": {"rss_bytes": 20, "system_used_bytes": 100, "system_swap_bytes": 5}},
            {"message": "Memory observation", "details": {"rss_bytes": 15, "system_used_bytes": 110, "system_swap_bytes": 8}},
            {"message": "Model worker runtime", "details": {"mlx_allocator_bytes": {"get_peak_memory": 30}}},
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "log.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\ntruncated\n", encoding="utf-8")
            result = diagnostic_metrics(path)
            self.assertEqual(result["stage_seconds"], {"asr": 5, "mix": 4})
            self.assertEqual(result["peak_worker_rss_bytes"], 20)
            self.assertEqual(result["peak_mlx_allocator_bytes"], 30)
            self.assertEqual(result["swap_growth_bytes"], 3)
            self.assertEqual(len(result["log_errors"]), 1)

    def test_model_probe_failures_continue_without_quality_claim(self):
        class FakeWorker:
            def __init__(self, role, *_): self.role = role
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def call(self, request):
                if self.role == "asr": raise ValueError("model error")
                return {"text": "你好"}
        with tempfile.TemporaryDirectory() as td:
            result = probe({"probes": [{"role": "asr", "request": {}}, {"role": "verifier", "request": {}}]}, {}, td, FakeWorker)
            self.assertEqual([r["status"] for r in result["probes"]], ["failed", "completed"])
            self.assertFalse(result["quality_verified"])
            self.assertFalse(result["hardware_performance_verified"])
        with self.assertRaises(ValueError):
            validate_result("aligner", {"items": [{"start_time": 0, "end_time": float("nan")}]})

    def test_setup_timeout_retains_readable_log(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "setup.log"
            with self.assertRaises(RuntimeError):
                checked_step([sys.executable, "-c", "import time;print('starting',flush=True);time.sleep(10)"], .1, path)
            self.assertIn("starting", path.read_text())

    def test_worker_deadline_covers_blocked_input_pipe(self):
        real_popen = subprocess.Popen
        def start(*args, **kwargs):
            return real_popen([sys.executable, "-c", "import time;time.sleep(10)"], **kwargs)
        started = time.monotonic()
        with patch("anime_dubber.quality_runtime.subprocess.Popen", side_effect=start):
            with Worker("asr", options({"mlx_python": sys.executable, "worker_timeout": 1}), CommandRunner()) as worker:
                with self.assertRaisesRegex(PipelineError, "deadline"):
                    worker.call({"text": "x" * 2000000})
            self.assertIsNotNone(worker.proc.poll())
            self.assertFalse(worker.writer.is_alive())
        self.assertLess(time.monotonic() - started, 6)

if __name__ == "__main__": unittest.main()
