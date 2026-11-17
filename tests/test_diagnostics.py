import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anime_dubber.application.diagnostics import DiagnosticLog


class DiagnosticTests(unittest.TestCase):
    def test_log_storage_failure_does_not_replace_processing_error(self):
        with tempfile.TemporaryDirectory() as td:
            log = DiagnosticLog(Path(td), "job_test", {})
            with patch.object(Path, "open", side_effect=OSError("disk full")):
                log.change_stage("transcribing")
                payload = log.write("Recognition failed", "error", {"traceback": "original model error"})
            self.assertEqual(payload["stage"], "transcribing")
            self.assertEqual(payload["details"]["traceback"], "original model error")
            self.assertEqual(payload["details"]["log_write_error"], "disk full")


    def test_stage_log_is_persisted_clean_and_redacted(self):
        with tempfile.TemporaryDirectory() as td:
            log = DiagnosticLog(Path(td), "job_test", {"elevenlabs_api_key": "secret-value", "model": "test"})
            log.change_stage("transcribing")
            log.write("\x1b[31mwarning\x1b[0m\rsecret-value", "warning",
                      {"nested": {"token": "private"}, "url": "https://example.test?v=x&token=private"})
            log.change_stage("translating")
            log.write("finished")
            text = log.path.read_text()
            self.assertIn("=== TRANSCRIBING ===", text)
            self.assertIn("duration_seconds", text)
            self.assertNotIn("secret-value", text)
            self.assertNotIn("private", text)
            self.assertNotIn("\x1b", text)
            rows = [json.loads(line) for line in log.json_path.read_text().splitlines()]
            self.assertEqual(rows[-1]["stage"], "translating")
