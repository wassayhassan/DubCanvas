import sys
import tempfile
import unittest
from pathlib import Path

from anime_dubber.core import download_source


class CP:
    def __init__(self, stdout=""):
        self.stdout = stdout


class FakeRunner:
    def __init__(self, work, failures=2):
        self.work = Path(work)
        self.failures = failures
        self.calls = []

    def run_stream(self, cmd, line_callback=None, **kwargs):
        self.calls.append(cmd)
        if len(self.calls) <= self.failures:
            raise RuntimeError("HTTP Error 403: Forbidden")
        out = self.work / "source.mp4"
        out.write_bytes(b"fake")
        if line_callback:
            line_callback("__YTDLP_PROGRESS__|100.0%|1.0MiB/s|00:00|1.0MiB")
        return CP("__YTDLP_FILE__:" + str(out) + "\n")



class YtDlpFallbackTests(unittest.TestCase):
    def test_retries_web_embedded_then_hls(self):
        with tempfile.TemporaryDirectory() as td:
            runner = FakeRunner(td, failures=2)
            got = download_source("https://youtu.be/example", Path(td), runner, lambda _: None)
            self.assertTrue(got.exists())
            self.assertEqual(len(runner.calls), 3)
            self.assertTrue(all("--force-ipv4" in cmd for cmd in runner.calls))
            self.assertEqual(runner.calls[0][:3], [sys.executable, "-m", "yt_dlp"])
            self.assertIn("youtube:player_client=web_embedded", runner.calls[1])
            self.assertIn("youtube:player_client=web_embedded", runner.calls[2])
            joined = " ".join(runner.calls[2])
            self.assertIn("96/95/94/93/92/91", joined)



if __name__ == "__main__":
    unittest.main()
