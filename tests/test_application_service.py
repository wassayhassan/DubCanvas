from __future__ import annotations

import unittest
from pathlib import Path
import json
import tempfile
from unittest.mock import patch

from anime_dubber.application.events import progress_to_event
from anime_dubber.application.service import ApplicationService, config_from_dict
from anime_dubber.cli import build_parser
from anime_dubber.application.project import ProjectStore
from anime_dubber.core import ReviewRequired


class ApplicationServiceTests(unittest.TestCase):
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
            with patch("anime_dubber.application.service.os.kill", side_effect=ProcessLookupError):
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
            with patch("anime_dubber.application.service.os.kill", return_value=None):
                project = service.get_project(temp, store.project_id)
            self.assertEqual(project["status"], "running")
            self.assertEqual(store.load()["active_job_id"], "job_live")

    def test_macos_system_check_uses_reported_multilingual_availability(self):
        service = ApplicationService()
        caps = {
            "providers": {
                "asr": {"mlx_whisper": True},
                "tts": {"chatterbox": True, "chatterbox_multilingual": False,
                        "kokoro": True, "macos": True},
                "stems": {"demucs": True},
            }
        }
        with patch("anime_dubber.application.service.platform.system", return_value="Darwin"), \
             patch.object(service, "capabilities", return_value=caps), \
             patch("anime_dubber.application.service.shutil.which", return_value="/usr/bin/tool"), \
             patch("anime_dubber.application.service._module_available", return_value=True):
            for available in (False, True):
                caps["providers"]["tts"]["chatterbox_multilingual"] = available
                checks = service.system_check()["checks"]
                multilingual = next(check for check in checks if check["name"] == "Chatterbox Multilingual")
                self.assertEqual(multilingual["ok"], available)

    def test_new_jobs_default_to_automatic_review_and_stronger_local_models(self):
        cfg = config_from_dict({"source": "example.mp4", "output_dir": "/tmp/anime-review"})
        self.assertTrue(cfg.review_before_dub)
        self.assertEqual(cfg.mlx_whisper_model, "mlx-community/whisper-large-v3-mlx")
        self.assertEqual(cfg.llm_model, "mlx-community/Qwen3-8B-4bit")
        default_args = build_parser().parse_args(["run", "example.mp4"])
        self.assertTrue(default_args.review_before_dub)
        self.assertEqual(default_args.llm_model, cfg.llm_model)
        self.assertEqual(default_args.mlx_whisper_model, cfg.mlx_whisper_model)
        self.assertFalse(build_parser().parse_args(["run", "example.mp4", "--no-auto-review"]).review_before_dub)

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

    def test_config_accepts_nested_v4_schema(self):
        cfg = config_from_dict({
            "source": "video.mp4",
            "output_dir": "~/AnimeDubberOut",
            "series_id": "series-a",
            "translation": {"provider": "mlx_llm"},
            "tts": {"provider": "macos", "fallback_voice": "Daniel", "rate": 220},
            "speaker_analysis": {
                "enabled": True,
                "backend": "auto",
                "max_speakers": 8,
                "threshold": None,
            },
            "audio": {
                "background_volume": 0.9,
                "dub_volume": 1.2,
                "ducking": False,
            },
        })
        self.assertEqual(cfg.translation, "llm")
        self.assertEqual(cfg.tts_engine, "macos")
        self.assertEqual(cfg.voice, "Daniel")
        self.assertEqual(cfg.tts_rate, 220)
        self.assertEqual(cfg.max_speakers, 8)
        self.assertEqual(cfg.speaker_threshold, 0.0)
        self.assertAlmostEqual(cfg.background_volume, 0.9)
        self.assertAlmostEqual(cfg.dub_volume, 1.2)
        self.assertTrue(str(cfg.output_dir).endswith("AnimeDubberOut"))

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

    def test_progress_download_is_structured(self):
        event = progress_to_event(
            "__DOWNLOAD_PROGRESS__|42.5|8.1MiB/s|00:31|1.2GiB|2|3",
            "job_x",
        )
        self.assertEqual(event.event, "progress")
        self.assertEqual(event.job_id, "job_x")
        self.assertEqual(event.data["stage"], "downloading")
        self.assertAlmostEqual(event.data["fraction"], 0.425)
        self.assertEqual(event.data["attempt"], "2")

    def test_progress_tts_count_becomes_fraction(self):
        event = progress_to_event("Generating English voice: 25/100 (spk_1, normal)", "job_x")
        self.assertEqual(event.data["stage"], "synthesizing")
        self.assertEqual(event.data["completed"], 25)
        self.assertEqual(event.data["total"], 100)
        self.assertAlmostEqual(event.data["fraction"], 0.25)

    def test_unreferenced_character_preview_uses_character_voice(self):
        service = ApplicationService()
        with patch("anime_dubber.providers.tts.kokoro_available", return_value=True), \
             patch("anime_dubber.providers.tts.synthesize_kokoro",
                   side_effect=lambda _text, path, **_kw: path.write_bytes(b"preview")) as synthesize:
            preview = service.preview_voice({
                "provider": "chatterbox", "character_id": "CHAR_001", "reference_audio": "",
                "voice_class": "female", "age_group": "child", "kokoro_voice": "auto",
            })
        self.assertEqual(preview["provider"], "kokoro")
        self.assertEqual(synthesize.call_args.kwargs["voice"], "af_heart")
        Path(preview["path"]).unlink(missing_ok=True)

    def test_post_voice_steps_report_their_own_progress(self):
        for title in ("Checking voice clips", "Rendering dub timeline", "Preparing music and effects"):
            with self.subTest(title=title):
                event = progress_to_event(f"{title}: 3/12", "job_x")
                self.assertEqual(event.data["stage"], "mixing")
                self.assertAlmostEqual(event.data["fraction"], 0.25)
        event = progress_to_event("Combining dub timeline chunks…", "job_x")
        self.assertEqual(event.data["stage"], "mixing")
        self.assertNotIn("fraction", event.data)

    def test_sync_job_wraps_legacy_pipeline(self):
        events = []
        service = ApplicationService(event_sink=events.append)

        def fake_pipeline(config, progress, runner):
            progress("Transcribing Mandarin…")
            progress("Generating English voice: 2/2")
            return {"dubbed_video": Path("/tmp/out.mp4")}

        with patch("anime_dubber.application.service.run_pipeline", side_effect=fake_pipeline):
            job = service.run_sync({
                "source": "video.mp4",
                "output_dir": "/tmp/out",
            })

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["dubbed_video"], str(Path("/tmp/out.mp4")))
        self.assertTrue(any(e.event == "job_started" for e in events))
        self.assertTrue(any(e.event == "artifact" for e in events))
        self.assertTrue(any(e.event == "finished" for e in events))


if __name__ == "__main__":
    unittest.main()
