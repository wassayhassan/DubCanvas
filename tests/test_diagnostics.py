import io
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from anime_dubber.application.diagnostics import DiagnosticLog, DiagnosticStream, capture_model_output
from anime_dubber.application.service import ApplicationService
from anime_dubber.core import CommandRunner


class DiagnosticTests(unittest.TestCase):
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

    def test_job_failure_preserves_failing_stage_and_chained_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            service = ApplicationService(events.append)
            def fail(config, progress, runner):
                progress("Transcribing source…")
                runner.diagnostic("Recognition output", raw_cues=4, detected_language="en")
                try:
                    raise ValueError("bad model tensor")
                except ValueError as exc:
                    raise RuntimeError("transcription failed") from exc
            with patch("anime_dubber.application.service.run_pipeline", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "transcription failed"):
                    service.run_sync({"source": "video.mp4", "output_dir": td})
            error = next(event for event in events if event.event == "error")
            self.assertEqual(error.data["stage"], "transcribing")
            self.assertIn("ValueError: bad model tensor", error.data["traceback"])
            self.assertIn("RuntimeError: transcription failed", Path(error.data["log_path"]).read_text())
            self.assertIn("raw_cues", Path(error.data["log_path"]).read_text())

    def test_model_output_is_scoped_to_job_thread(self):
        with tempfile.TemporaryDirectory() as td:
            log = DiagnosticLog(Path(td), "job_test", {})
            stream = DiagnosticStream(io.StringIO())
            with capture_model_output(log):
                stream.write("model detail")
                thread = threading.Thread(target=lambda: stream.write("unrelated thread"))
                thread.start(); thread.join()
            stream.write("after job")
            text = log.path.read_text()
            self.assertIn("model detail", text)
            self.assertNotIn("unrelated thread", text)
            self.assertNotIn("after job", text)

    def test_subprocess_diagnostics_include_successful_stderr_and_exit_code(self):
        messages = []
        runner = CommandRunner()
        runner.diagnostic = lambda message, **details: messages.append((message, details))
        with patch("anime_dubber.core.subprocess.Popen") as popen:
            popen.return_value.communicate.return_value = ("output", "model warning")
            popen.return_value.returncode = 0
            runner.run(["test-command"])
        self.assertEqual(messages[-1][1]["stderr"], "model warning")
        self.assertEqual(messages[-1][1]["returncode"], 0)
