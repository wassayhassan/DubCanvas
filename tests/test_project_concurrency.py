import multiprocessing
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anime_dubber.application.project import ProjectStore
from anime_dubber.application.service import ApplicationService, config_from_dict
from anime_dubber.application.project_lock import process_alive
from anime_dubber.cli import build_parser


def add_warnings(output, source, barrier):
    store = ProjectStore(Path(output), source)
    barrier.wait(timeout=20)
    for i in range(20):
        store.add_warning(f"{multiprocessing.current_process().name}-{i}")


def claim_job(output, source, barrier, results, release):
    service = ApplicationService()
    barrier.wait(timeout=20)
    with patch.object(ApplicationService, "_execute", lambda *args: None):
        try:
            service.start_job({"source": source, "output_dir": output, "mode": "subtitles"})
            results.put("accepted")
        except ValueError:
            results.put("busy")
        except Exception as exc:
            results.put(str(exc))
        release.wait(timeout=20)


class ProjectConcurrencyTests(unittest.TestCase):
    def test_liveness_check_never_terminates_the_target(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(process_alive(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(process_alive(child.pid))

    def test_processes_preserve_all_manifest_updates(self):
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as td:
            source = str(Path(td) / "video.mp4")
            store = ProjectStore(Path(td), source)
            store.create(source=source, name="Video")
            barrier = ctx.Barrier(2)
            children = [ctx.Process(target=add_warnings, args=(td, source, barrier)) for _ in range(2)]
            for child in children:
                child.start()
            try:
                for child in children:
                    child.join(timeout=30)
                    self.assertEqual(child.exitcode, 0)
                self.assertEqual(len(store.load()["warnings"]), 40)
                self.assertFalse(list(store.manifest_path.parent.glob("*.tmp")))
            finally:
                for child in children:
                    if child.is_alive():
                        child.terminate()
                    child.join()

    def test_only_one_process_can_claim_a_project(self):
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as td:
            source = str(Path(td) / "video.mp4")
            ProjectStore(Path(td), source).create(source=source, name="Video")
            barrier, results, release = ctx.Barrier(2), ctx.Queue(), ctx.Event()
            children = [ctx.Process(target=claim_job, args=(td, source, barrier, results, release)) for _ in range(2)]
            for child in children:
                child.start()
            try:
                self.assertEqual(sorted([results.get(timeout=30), results.get(timeout=30)]), ["accepted", "busy"])
            finally:
                release.set()
                for child in children:
                    child.join(timeout=5)
                    if child.is_alive():
                        child.terminate()
                        child.join()
                results.close()

    def test_kokoro_defaults_and_cloning_opt_in(self):
        config = config_from_dict({"source": "video.mp4", "output_dir": "."})
        self.assertEqual(config.tts_engine, "kokoro")
        self.assertFalse(config.auto_voice_references)
        explicit = config_from_dict({"source": "video.mp4", "output_dir": ".",
                                     "tts": {"provider": "chatterbox", "auto_voice_references": True}})
        self.assertEqual(explicit.tts_engine, "chatterbox")
        self.assertTrue(explicit.auto_voice_references)
        args = build_parser().parse_args(["run", "video.mp4"])
        self.assertEqual(args.tts, "kokoro")
        self.assertFalse(args.auto_source_voices)
        args = build_parser().parse_args(["run", "video.mp4", "--tts", "chatterbox", "--auto-source-voices"])
        self.assertTrue(args.auto_source_voices)
