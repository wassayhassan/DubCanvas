import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from anime_dubber.core import CommandRunner, PipelineError
from anime_dubber.quality_models import options
from anime_dubber.quality_models import verify_translation_adapter, file_digest
from anime_dubber.quality_runtime import Worker
from tools.evaluate_quality import evaluate


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

    def test_empty_corpus_cannot_pass(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ValueError):
            evaluate({"clips": []}, {}, td)


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
