import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from anime_dubber.application.service import ApplicationService
from anime_dubber.core import (Config, Segment, run_pipeline, translate_with_llm,
                               translate_with_ollama_provider, LLM_MODEL, CommandRunner,
                               PipelineError, _version_profiles)
from anime_dubber.application.service import config_from_dict
import hashlib
import json
import sys
import types


class ProjectVersionsTests(unittest.TestCase):
    def test_untranslated_cache_is_repaired_instead_of_dubbed(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config(source="source.mp4", output_dir=Path(temp), translation="llm")
            signature = hashlib.sha1(json.dumps({
                "model": LLM_MODEL, "target_language": "en", "context": cfg.context,
                "glossary": cfg.glossary, "source_text": ["天下武林,门派如林"],
            }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            cache = Path(temp) / f"translations_llm_{signature}.json"
            cache.write_text(json.dumps({"0": "天下武林,门派如林"}, ensure_ascii=False), encoding="utf-8")
            responses = iter(['[{"id": 0, "text": "The martial world has countless sects."}]'])
            fake = types.SimpleNamespace(
                load=lambda _: (object(), types.SimpleNamespace(chat_template=None)),
                generate=lambda *_args, **_kwargs: next(responses))
            with patch.dict(sys.modules, {"mlx_lm": fake}):
                result = translate_with_llm([Segment(0, 2, "天下武林,门派如林")], cfg,
                                            Path(temp), CommandRunner(), lambda _: None)
            self.assertEqual(result[0].translated, "The martial world has countless sects.")
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["0"], result[0].translated)

    def test_ollama_never_caches_untranslated_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config(source="source.mp4", output_dir=Path(temp), translation="ollama")
            with patch("anime_dubber.providers.translation.translate_with_ollama",
                       return_value={0: "天下武林,门派如林"}), \
                 patch("anime_dubber.providers.translation.ollama_generate",
                       return_value="The martial world has countless sects."):
                result = translate_with_ollama_provider([Segment(0, 2, "天下武林,门派如林")],
                                                        cfg, Path(temp), CommandRunner(), lambda _: None)
            self.assertEqual(result[0].translated, "The martial world has countless sects.")
            self.assertNotIn("天下武林", next(Path(temp).glob("translations_ollama_*.json")).read_text(encoding="utf-8"))

    def test_translation_failure_stops_before_dub(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = Config(source="source.mp4", output_dir=Path(temp), translation="llm")
            fake = types.SimpleNamespace(
                load=lambda _: (object(), types.SimpleNamespace(chat_template=None)),
                generate=lambda *_args, **_kwargs: '[{"id": 0, "text": "唐门"}]')
            with patch.dict(sys.modules, {"mlx_lm": fake}):
                with self.assertRaisesRegex(PipelineError, "Translation failed for line 1"):
                    translate_with_llm([Segment(0, 1, "唐门")], cfg, Path(temp),
                                       CommandRunner(), lambda _: None)
            self.assertFalse(any(Path(temp).glob("translations_llm_*.json")))

    def test_voice_choices_are_version_scoped(self):
        shared = types.SimpleNamespace(id="speaker_1", kokoro_voice="af_heart",
                                       to_dict=lambda: {"id": "speaker_1", "kokoro_voice": "af_heart"})
        first = _version_profiles([shared], {"speaker_1": {"tts_provider": "kokoro", "kokoro_voice": "am_adam"}})
        second = _version_profiles([shared], {"speaker_1": {"tts_provider": "chatterbox"}})
        self.assertEqual(shared.kokoro_voice, "af_heart")
        self.assertEqual(first["speaker_1"]["kokoro_voice"], "am_adam")
        self.assertEqual(second["speaker_1"]["tts_provider"], "chatterbox")
        self.assertEqual(config_from_dict({"source": "source.mp4", "output_dir": "/tmp",
                                           "voice_overrides": {"speaker_1": {"tts_provider": "kokoro"}}})
                         .voice_overrides["speaker_1"]["tts_provider"], "kokoro")

    def test_failed_dub_retries_in_place_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            first_service = ApplicationService()
            project = first_service.create_project(temp, "video.mp4", "Video")
            calls = []

            def interrupted(config, progress, runner):
                calls.append(config.version_id)
                raise RuntimeError("TTS line 50 failed")

            with patch("anime_dubber.application.service.run_pipeline", side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, "TTS line 50 failed"):
                    first_service.run_sync({"source": "video.mp4", "output_dir": temp})
            dub = first_service.get_project(temp, project["project_id"])["dubs"][0]
            # Simulate a version made before source voice selection existed.
            manifest = Path(first_service.get_project(temp, project["project_id"])["manifest_path"])
            old_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            old_manifest["dubs"][0]["config"].pop("auto_voice_references", None)
            manifest.write_text(json.dumps(old_manifest), encoding="utf-8")
            resumed = ApplicationService()
            def resume_pipeline(cfg, progress, runner):
                self.assertFalse(cfg.auto_voice_references)
                calls.append(cfg.version_id)
                return {}
            with patch("anime_dubber.application.service.run_pipeline", side_effect=resume_pipeline):
                job_id = resumed.resume_dub(temp, project["project_id"], dub["id"])
                for _ in range(100):
                    if resumed.get_job(job_id)["status"] not in {"queued", "running"}:
                        break
                    time.sleep(.01)
            self.assertEqual(resumed.get_job(job_id)["status"], "completed")
            self.assertEqual(calls, [dub["id"], dub["id"]])
            self.assertEqual(len(resumed.get_project(temp, project["project_id"])["dubs"]), 1)
            for _ in range(100):
                if resumed.get_project(temp, project["project_id"])["status"] == "completed":
                    break
                time.sleep(.01)

    def test_pause_then_resume_reuses_the_same_version_and_keeps_subtitles(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            service = ApplicationService()
            project = service.create_project(str(output), "source.mp4", "Episode")
            at_voice = threading.Event()
            calls = []

            def pipeline(config, progress, runner):
                calls.append(config.version_id)
                subtitles = output / "versions" / config.version_id / "en.srt"
                subtitles.parent.mkdir(parents=True, exist_ok=True)
                if not subtitles.exists():
                    subtitles.write_text("Translation finished")
                runner.artifact("translated_srt", subtitles, "en")
                if len(calls) == 1:
                    at_voice.set()
                    while not runner.cancel_event.wait(.01):
                        pass
                    runner.check_cancel()
                self.assertTrue(config.resume)
                self.assertFalse(config.force)
                return {"translated_srt": subtitles}

            with patch("anime_dubber.application.service.run_pipeline", side_effect=pipeline):
                first = service.start_job({"source": "source.mp4", "output_dir": temp})
                self.assertTrue(at_voice.wait(2))
                self.assertTrue(service.pause_job(first))
                for _ in range(100):
                    if service.get_job(first)["status"] == "paused":
                        break
                    time.sleep(.01)
                self.assertEqual(service.get_job(first)["status"], "paused")
                state = service.get_project(temp, project["project_id"])
                self.assertTrue(Path(state["dubs"][0]["artifacts"]["translated_srt"]).exists())
                dub_id = state["dubs"][0]["id"]
                second = service.resume_dub(temp, project["project_id"], dub_id)
                for _ in range(100):
                    if service.get_job(second)["status"] == "completed":
                        break
                    time.sleep(.01)
            self.assertEqual(service.get_job(second)["status"], "completed")
            self.assertEqual(calls, [dub_id, dub_id])
            self.assertEqual(len(service.get_project(temp, project["project_id"])["dubs"]), 1)

    def test_sync_job_cannot_overwrite_an_active_project(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ApplicationService()
            started = threading.Event()
            release = threading.Event()

            def pipeline(config, progress, runner):
                started.set()
                self.assertTrue(release.wait(3))
                return {}

            with patch("anime_dubber.application.service.run_pipeline", side_effect=pipeline):
                job_id = service.start_job({"source": "source.mp4", "output_dir": temp})
                try:
                    self.assertTrue(started.wait(2))
                    with self.assertRaisesRegex(ValueError, "already running"):
                        service.run_sync({"source": "source.mp4", "output_dir": temp})
                    self.assertEqual(len(service.list_projects(temp)[0]["dubs"]), 1)
                finally:
                    release.set()
                for _ in range(100):
                    if service.get_job(job_id)["status"] == "completed":
                        break
                    time.sleep(.01)
                self.assertEqual(service.get_job(job_id)["status"], "completed")

    def test_old_english_translation_cache_is_reused_without_loading_model(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Config(source="source.mp4", output_dir=Path(temp), translation="llm")
            segments = [Segment(0, 1, "你好")]
            signature = hashlib.sha1(json.dumps({
                "model": LLM_MODEL, "context": config.context,
                "glossary": config.glossary, "source_text": ["你好"],
            }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            Path(temp, f"translations_llm_{signature}.json").write_text('{"0":"Hello"}')
            result = translate_with_llm(segments, config, Path(temp), CommandRunner(), lambda _: None)
            self.assertEqual(result[0].translated, "Hello")
            # Migration writes a new signature but preserves the old artifact.
            self.assertEqual(len(list(Path(temp).glob("translations_llm_*.json"))), 2)

    def test_multiple_dubs_preserve_versions_and_deletion_is_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            service = ApplicationService()
            project = service.create_project(str(output), "source.mp4", "Episode One")

            def fake_pipeline(config, progress, runner):
                folder = output / "versions" / config.version_id
                folder.mkdir(parents=True)
                subtitles = folder / "translation.srt"
                subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
                runner.artifact("translated_srt", subtitles, config.target_language)
                video = folder / "final.mp4"
                video.write_bytes(config.tts_engine.encode())
                return {"translated_srt": subtitles, "dubbed_video": video}

            with patch("anime_dubber.application.service.run_pipeline", side_effect=fake_pipeline):
                service.run_sync({"source": "source.mp4", "output_dir": str(output), "dub_name": "Version A", "tts": {"provider": "macos"}})
                service.run_sync({"source": "source.mp4", "output_dir": str(output), "dub_name": "Version B", "tts": {"provider": "kokoro"}})

            detail = service.get_project(str(output), project["project_id"])
            self.assertEqual(len(detail["dubs"]), 2)
            self.assertEqual(len(detail["subtitles"]), 2)
            first, second = detail["dubs"]
            first_video = Path(first["artifacts"]["dubbed_video"])
            second_video = Path(second["artifacts"]["dubbed_video"])
            self.assertNotEqual(first_video, second_video)
            self.assertEqual(first_video.read_bytes(), b"macos")
            self.assertEqual(second_video.read_bytes(), b"kokoro")
            service.delete_dub(str(output), project["project_id"], first["id"])
            self.assertFalse(first_video.exists())
            self.assertTrue(second_video.exists())
            self.assertEqual(len(service.get_project(str(output), project["project_id"])["dubs"]), 1)
            service.delete_dub(str(output), project["project_id"], second["id"])
            final = service.get_project(str(output), project["project_id"])
            self.assertNotIn("dubbed_video", final["artifacts"])
            self.assertNotIn("translated_srt", final["artifacts"])

    def test_deleting_latest_dub_restores_previous_output_links(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ApplicationService()
            project = service.create_project(temp, "source.mp4", "Episode", "saved-series")

            def fake_pipeline(config, progress, runner):
                self.assertEqual(config.series_id, "saved-series")
                folder = Path(temp) / "versions" / config.version_id
                folder.mkdir(parents=True)
                video = folder / "final.mp4"
                video.write_bytes(b"video")
                runner.artifact("dubbed_video", video, "en")
                return {"dubbed_video": video}

            with patch("anime_dubber.application.service.run_pipeline", side_effect=fake_pipeline):
                service.run_sync({"source": "source.mp4", "output_dir": temp})
                service.run_sync({"source": "source.mp4", "output_dir": temp})
            before = service.get_project(temp, project["project_id"])
            first, second = before["dubs"]
            self.assertEqual(before["series_id"], "saved-series")
            self.assertEqual(before["artifacts"]["dubbed_video"], second["artifacts"]["dubbed_video"])
            service.delete_dub(temp, project["project_id"], second["id"])
            after = service.get_project(temp, project["project_id"])
            self.assertEqual(after["artifacts"]["dubbed_video"], first["artifacts"]["dubbed_video"])

    def test_srt_and_vtt_are_published_before_voice_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            video = output / "source.mp4"; video.write_bytes(b"video")
            audio = output / "audio.wav"; audio.write_bytes(b"audio")
            events = []
            class Runner:
                def check_cancel(self): pass
                def artifact(self, kind, path, language):
                    events.append((kind, Path(path).exists(), language))

            def translation(segments, *_args):
                segments[0].translated = "Hello"
                return segments

            with patch("anime_dubber.core.download_source", return_value=video), \
                 patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.transcribe_audio", return_value=[Segment(0, 1, "你好")]), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translation):
                config = Config(source=str(video), output_dir=output, mode="subtitles", translation="llm", version_id="sub_test")
                results = run_pipeline(config, lambda _: None, Runner())

            self.assertEqual([e[0] for e in events], ["source_video", "chinese_srt", "chinese_vtt", "translated_srt", "translated_vtt", "review_report"])
            self.assertTrue(all(exists for _, exists, _ in events))
            self.assertIn("WEBVTT", results["translated_vtt"].read_text())
            self.assertIn("Hello", results["translated_srt"].read_text())
            self.assertEqual(json.loads(results["review_report"].read_text())["total_cues"], 1)
            self.assertEqual(results["translated_srt"].parent.name, "sub_test")

    def test_translated_subtitles_survive_character_analysis_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            video = output / "source.mp4"
            video.write_bytes(b"video")
            audio = output / "audio.wav"
            audio.write_bytes(b"audio")
            service = ApplicationService()
            project = service.create_project(temp, str(video), "Source")

            def translation(segments, *_args):
                segments[0].translated = "Hello"
                return segments

            def fail_analysis(*_args, **_kwargs):
                raise RuntimeError("speaker model failed")

            character_module = types.ModuleType("anime_dubber.characters")
            character_module.analyze_characters = fail_analysis
            character_module.write_character_map = lambda *_args: None
            with patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(audio, audio)), \
                 patch("anime_dubber.core.transcribe_audio", return_value=[Segment(0, 1, "你好")]), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translation), \
                 patch.dict(sys.modules, {"anime_dubber.characters": character_module}):
                with self.assertRaisesRegex(RuntimeError, "speaker model failed"):
                    service.run_sync({"source": str(video), "output_dir": temp,
                                      "translation": "llm", "tts_engine": "chatterbox"})

            dub = service.get_project(temp, project["project_id"])["dubs"][0]
            self.assertEqual(dub["status"], "failed")
            self.assertIn("Hello", Path(dub["artifacts"]["translated_srt"]).read_text(encoding="utf-8"))
            self.assertTrue(Path(dub["artifacts"]["translated_vtt"]).exists())

    def test_non_english_voice_constraints_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ApplicationService()
            self.assertEqual(service.capabilities()["providers"]["tts"]["chatterbox_multilingual"],
                             __import__("anime_dubber.providers.tts", fromlist=["multilingual_chatterbox_available"]).multilingual_chatterbox_available())
            with self.assertRaisesRegex(ValueError, "Chatterbox Multilingual or ElevenLabs"):
                service.run_sync({"source": "source.mp4", "output_dir": temp, "target_language": "es", "tts": {"provider": "kokoro"}})
            with self.assertRaisesRegex(ValueError, "Whisper direct"):
                service.run_sync({"source": "source.mp4", "output_dir": temp, "source_language": "zh", "target_language": "es", "mode": "subtitles", "translation": {"provider": "whisper"}})


if __name__ == "__main__":
    unittest.main()
