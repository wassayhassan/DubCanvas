from __future__ import annotations

import unittest
from pathlib import Path
import json
import tempfile
import shutil
import subprocess
from unittest.mock import patch

from anime_dubber.application.service import ApplicationService, config_from_dict
from anime_dubber.application.project import ProjectStore
from anime_dubber.core import ReviewRequired


class ApplicationServiceTests(unittest.TestCase):
    def test_source_inspection_checks_real_local_audio_and_video(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg tools unavailable")
        with tempfile.TemporaryDirectory() as td:
            video = Path(td) / "episode.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=160x120:d=1", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=1", "-shortest", "-c:v", "mpeg4",
                            "-c:a", "aac", str(video)], check=True)
            info = ApplicationService().inspect_source(str(video))
            self.assertEqual(info["title"], "episode.mp4")
            self.assertGreater(info["duration"], 0.9)
            self.assertGreater(info["size_bytes"], 0)
            self.assertAlmostEqual(info["modified_at"], video.stat().st_mtime, places=3)
            video.unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                ApplicationService().inspect_source(str(video))

    def test_source_inspection_rejects_unsupported_and_transient_links(self):
        service = ApplicationService()
        with self.assertRaisesRegex(ValueError, "http or https"):
            service.inspect_source("ftp://example.com/movie")
        with self.assertRaisesRegex(ValueError, "Temporary playback"):
            service.inspect_source("https://r1---sn.googlevideo.com/videoplayback?id=abc")
        with patch("anime_dubber.application.service._module_available", return_value=True), \
             patch("anime_dubber.application.service.subprocess.run", return_value=subprocess.CompletedProcess(
                 [], 0, json.dumps({"title": "Episode", "duration": 72,
                                    "formats": [{"url": "https://example.com/video.mp4", "vcodec": "h264", "acodec": "aac"}]}), "")):
            info = service.inspect_source("https://example.com/watch?v=1")
        self.assertEqual(info["duration"], 72)
        self.assertEqual(info["kind"], "url")
        with patch("anime_dubber.application.service._module_available", return_value=True), \
             patch("anime_dubber.application.service.subprocess.run", return_value=subprocess.CompletedProcess(
                 [], 0, json.dumps({"title": "Silent episode", "formats": [
                     {"vcodec": "h264", "acodec": "none"}]}), "")):
            with self.assertRaisesRegex(ValueError, "No audio track"):
                service.inspect_source("https://example.com/watch?v=2")


    def test_replacing_source_hides_old_shared_analysis_but_keeps_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ProjectStore(Path(temp), "video.mp4")
            store.create(source="video.mp4", name="Video")
            store.begin(job_id="old", kind="run", dub_id="old_dub",
                        config={"source": "video.mp4", "output_dir": temp, "target_language": "en"})
            source_srt = Path(temp) / "source.srt"; source_srt.write_text("old text")
            translated = Path(temp) / "translation.srt"; translated.write_text("old translation")
            store.publish_artifact("source_srt", str(source_srt), language="en")
            store.publish_artifact("translated_srt", str(translated), dub_id="old_dub",
                                   version_id="old_dub", language="en")
            store.finish(status="completed", dub_id="old_dub")
            store.begin(job_id="new", kind="run", dub_id="new_dub",
                        config={"source": "video.mp4", "output_dir": temp, "target_language": "en"})
            old_revision = store.load()["analysis_revision"]
            store.invalidate_shared_source()
            project = store.load()
            self.assertNotIn("source_srt", project["artifacts"])
            self.assertFalse(any(key.startswith("source:") for key in project["subtitles"]))
            self.assertEqual(project["dubs"][0]["artifacts"]["translated_srt"], str(translated))
            self.assertEqual(project["dubs"][1]["artifacts"], {})
            self.assertGreater(project["analysis_revision"], old_revision)

    def test_selected_missing_speech_provider_is_rejected_before_version_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ApplicationService()
            store = ProjectStore(Path(temp), "video.mp4")
            store.create(source="video.mp4", name="Video")
            providers = {"asr": {"mlx_whisper": False, "faster_whisper": False},
                         "translation": {"mlx_llm": True, "ollama": False},
                         "tts": {"chatterbox": True, "chatterbox_multilingual": False,
                                 "kokoro": False, "macos": True, "piper": False},
                         "stems": {"demucs": True}}
            with patch.object(service, "capabilities", return_value={"providers": providers}), \
                 patch("anime_dubber.application.service.shutil.which", return_value="/usr/bin/tool"):
                with self.assertRaisesRegex(ValueError, "speech recognition"):
                    service.start_job({"source": "video.mp4", "output_dir": temp,
                                       "verify_setup": True, "asr": {"provider": "mlx_whisper"}})
            self.assertEqual(store.load()["dubs"], [])

    def test_dead_backend_job_becomes_resumable_without_losing_subtitles(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ApplicationService()
            store = ProjectStore(Path(temp), "source.mp4")
            store.create(source="source.mp4", name="Episode")
            store.begin(job_id="job_old", kind="run", dub_id="dub_old",
                        config={"source": "source.mp4", "output_dir": temp,
                                "version_id": "dub_old", "target_language": "en", "keep_work": True})
            subtitle = Path(temp) / "versions" / "dub_old" / "english.srt"
            subtitle.parent.mkdir(parents=True)
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
            store.publish_artifact("translated_srt", str(subtitle), dub_id="dub_old",
                                   version_id="dub_old", language="en")
            with patch("anime_dubber.application.service.process_alive", return_value=False):
                project = service.list_projects(temp)[0]
            self.assertEqual(project["status"], "paused")
            self.assertEqual(project["dubs"][0]["status"], "paused")
            self.assertEqual(project["dubs"][0]["artifacts"]["translated_srt"], str(subtitle))
            self.assertIsNone(store.load()["active_job_id"])

    def test_live_external_backend_is_not_marked_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ApplicationService()
            store = ProjectStore(Path(temp), "source.mp4")
            store.create(source="source.mp4", name="Episode")
            store.begin(job_id="job_live", kind="run", dub_id="dub_live",
                        config={"source": "source.mp4", "output_dir": temp})
            with patch("anime_dubber.application.service.process_alive", return_value=True):
                project = service.get_project(temp, store.project_id)
            self.assertEqual(project["status"], "running")
            self.assertEqual(store.load()["active_job_id"], "job_live")


    def test_review_checkpoint_is_paused_and_approval_is_scoped_to_its_dub(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ApplicationService()
            source = "source.mp4"
            store = ProjectStore(Path(temp), source)
            project = store.create(source=source, name="Episode")
            with patch("anime_dubber.application.service.run_pipeline", side_effect=ReviewRequired("Subtitle review required before voice generation")):
                result = service.run_sync({"source": source, "output_dir": temp, "review_before_dub": True})
            self.assertEqual(result["status"], "paused")
            dub = service.get_project(temp, project["project_id"])["dubs"][0]
            report = Path(temp) / "versions" / dub["id"] / "source_en.review.json"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({"signature": "signed", "priority_cues": [1]}))
            store.publish_artifact("review_report", str(report), dub_id=dub["id"], version_id=dub["id"])
            approved = service.approve_review(temp, project["project_id"], dub["id"], {"1": "Approved text"})
            self.assertEqual(approved["revisions"], 1)
            self.assertEqual(json.loads(Path(approved["path"]).read_text())["revisions"], {"1": "Approved text"})
            with self.assertRaises(ValueError):
                service.approve_review(temp, project["project_id"], dub["id"], {"2": "Unrelated cue"})


    def test_job_snapshot_redacts_elevenlabs_key(self):
        service = ApplicationService()
        cfg = config_from_dict({
            "source": "video.mp4",
            "output_dir": "/tmp/out",
            "tts": {
                "provider": "elevenlabs",
                "api_key": "secret-value",
            },
        })
        normalized = service._normalized_config_dict(cfg)
        self.assertEqual(normalized["elevenlabs_api_key"], "<redacted>")


if __name__ == "__main__":
    unittest.main()
