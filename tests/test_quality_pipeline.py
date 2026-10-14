import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from anime_dubber.core import Config, CommandRunner, Segment, PipelineError, run_pipeline, analyze_only
from anime_dubber.quality_models import options, digest
from anime_dubber.quality_runtime import Checkpoints, Worker
from anime_dubber.quality_timing import choose_tempo, window, speech_error, validate_schedule, protected_speech_matches, chinese_critical_matches
from anime_dubber.localization import Glossary, localize, validate_review, parse_object, cloud_review
from anime_dubber.application.service import config_from_dict, ApplicationService

class QualityConfigTests(unittest.TestCase):
    def test_subtitles_and_analysis_do_not_require_unrelated_voice_resources(self):
        from anime_dubber.quality_models import setup_issues
        q = options({"voice_engine": "index_clone"})
        with patch("anime_dubber.quality_models.shutil.which", return_value=None):
            issues = setup_issues(q, require_platform=False, mode="subtitles")
            self.assertFalse(any("rubberband" in issue or "clone_python" in issue for issue in issues))
            issues = setup_issues(q, require_platform=False, analysis=True)
            self.assertFalse(any("Hy adapter" in issue or "translation_python" in issue or "review_python" in issue for issue in issues))
    def test_consent_is_explicit_boolean_not_truthy_text(self):
        for key in ("allow_cloud", "reference_consent"):
            with self.assertRaises(ValueError):
                options({key: "false"})
    def test_legacy_profile_preserved_and_quality_settings_roundtrip(self):
        self.assertEqual(config_from_dict({"source": "x", "output_dir": "/tmp/out"}).pipeline_profile, "legacy")
        cfg = config_from_dict({"source": "x", "output_dir": "/tmp/out", "pipeline_profile": "quality_v1",
            "quality_settings": {"voice_engine": "kokoro"}, "voice_overrides": {"s": {"qwen_speaker": "Ryan"}}})
        saved = ApplicationService()._normalized_config_dict(cfg)
        restored = config_from_dict(saved)
        self.assertEqual(restored.quality_settings["voice_engine"], "kokoro")
        self.assertEqual(restored.voice_overrides["s"]["qwen_speaker"], "Ryan")

    def test_rejects_invalid_profiles_speed_context_and_unknown_settings(self):
        for value in ({"maximum_speed": 1.51}, {"context_tokens": 16000}, {"worker_memory_gb": 48},
                      {"voice_engine": "magic"}, {"unrecognized_setting": True}):
            with self.assertRaises(ValueError):
                options(value)

    def test_quality_profile_enters_actual_workflow(self):
        with patch("anime_dubber.quality_pipeline.run_quality", return_value={"translated_srt": Path("a")}) as run:
            cfg = Config("x", Path("/tmp/out"), pipeline_profile="quality_v1")
            run_pipeline(cfg)
            analyze_only(cfg)
            self.assertEqual(run.call_count, 2)
            self.assertTrue(run.call_args.kwargs["analysis"])

    def test_cloud_consent_prevents_network_call(self):
        with patch("urllib.request.urlopen") as upload:
            with self.assertRaisesRegex(ValueError, "disabled"):
                cloud_review("private dialogue", options())
            upload.assert_not_called()

class TimingTests(unittest.TestCase):
    def test_spoken_numbers_match_digits_without_accepting_changed_quantities(self):
        self.assertEqual(speech_error("Level 3", "Level three"), 0)
        self.assertEqual(speech_error("120 years", "one hundred twenty years"), 0)
        self.assertTrue(protected_speech_matches("Level 3", "Level three"))
        self.assertFalse(protected_speech_matches("Level 3", "Level four"))
        self.assertTrue(protected_speech_matches("1.5 years", "one point five years"))
        self.assertTrue(protected_speech_matches("21st realm", "twenty first realm"))
    def test_low_cer_cannot_hide_source_negation_numbers_or_known_names(self):
        self.assertFalse(chinese_critical_matches("我没有背叛宗门", "我有背叛宗门", {}))
        self.assertFalse(chinese_critical_matches("十年之后", "四年之后", {}))
        self.assertFalse(chinese_critical_matches("林风来了", "林峰来了", {"林风": "Lin Feng"}))
        self.assertTrue(chinese_critical_matches("十二年之后", "12年之后", {}))
        self.assertTrue(chinese_critical_matches("一百二十年", "120年", {}))
        self.assertTrue(chinese_critical_matches("一万亿年", "1000000000000年", {}))
        self.assertFalse(chinese_critical_matches("一万亿年", "一亿一万年", {}))
    def test_low_wer_cannot_hide_negation_or_number_loss(self):
        self.assertFalse(protected_speech_matches("I did not reach realm 3", "I did reach realm 3"))
        self.assertFalse(protected_speech_matches("realm 3", "realm 4"))
        self.assertTrue(protected_speech_matches("I will never leave", "I will never leave"))
    def test_combined_ceiling_and_style_limits(self):
        q = options()
        self.assertIsNone(choose_tempo(1.6, 1, "normal", q))
        self.assertIsNone(choose_tempo(1.15, 1, "whispering", q))
        self.assertEqual(choose_tempo(1.1, 1, "normal", q), 1.1)
        q["allow_rescue_speed"] = True
        self.assertEqual(choose_tempo(1.5, 1, "normal", q, rescue=True), 1.5)
        self.assertIsNone(choose_tempo(1.4, 1, "normal", q, native_acceleration=1.1, rescue=True))

    def test_silence_borrow_stops_at_speaker_and_cut(self):
        segs = [Segment(0, 1, "a", speaker_id="a"), Segment(1.1, 2, "b", speaker_id="b")]
        self.assertAlmostEqual(window(segs, 0, 3, [], options()), 1.02)
        self.assertAlmostEqual(window(segs, 0, 3, [1.0], options()), 1)

    def test_preserves_intentional_overlap_rejects_invented_overlap(self):
        segs = [Segment(0, 1.5, "a", speaker_id="a"), Segment(1, 2, "b", speaker_id="b")]
        validate_schedule(segs, [1.5, 1], [])
        segs[0].end = .8
        with self.assertRaises(PipelineError):
            validate_schedule(segs, [1.5, 1], [])

    def test_missing_words_and_hallucinated_words_fail_qa(self):
        self.assertGreater(speech_error("I did not betray the sect", "I betrayed the sect"), .25)
        self.assertEqual(speech_error("Hello!", "hello"), 0)

class CheckpointTests(unittest.TestCase):
    def test_cache_invalidation_and_incomplete_cache_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            cp = Checkpoints(td, "source-a", Config("x", Path(td)), CommandRunner())
            inputs = {"model": "a", "revision": "sha-a", "glossary": "v1"}
            cp.put("translation", inputs, {"text": "Hello"})
            self.assertEqual(cp.get("translation", inputs)["text"], "Hello")
            self.assertIsNone(cp.get("translation", {**inputs, "revision": "sha-b"}))
            other = Checkpoints(td, "source-b", Config("x", Path(td)), CommandRunner())
            self.assertIsNone(other.get("translation", inputs))
            changed_runtime = Checkpoints(td, "source-a", Config("x", Path(td)), CommandRunner(), {"mlx": "new"})
            self.assertIsNone(changed_runtime.get("translation", inputs))
            path = cp.path("translation", inputs)
            path.write_text('{"complete": false}')
            self.assertIsNone(cp.get("translation", inputs))

class LocalizationTests(unittest.TestCase):
    def test_default_preset_is_connected_and_explicit_female_qwen_choice_is_preserved(self):
        from anime_dubber.quality_pipeline import cast
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); path = root / "cast.json"
            cfg = Config("source", root)
            profiles = cast([Segment(0, 1, "你好", speaker_id="s")], cfg,
                            options({"qwen_speaker": "Vivian"}), path, CommandRunner())
            self.assertEqual(profiles["s"]["qwen_speaker"], "Vivian")
            payload = json.loads(path.read_text())
            payload["characters"][0].update(voice_class="female", tts_provider="qwen", manual=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            profiles = cast([Segment(0, 1, "你好", speaker_id="s")], cfg, options(), path, CommandRunner())
            self.assertEqual(profiles["s"]["tts_provider"], "qwen")
    def test_glossary_roundtrips_under_windows_default_encoding(self):
        real_read = Path.read_text
        def windows_read(path, *args, **kwargs):
            kwargs.setdefault("encoding", "cp1252")
            return real_read(path, *args, **kwargs)
        with tempfile.TemporaryDirectory() as td, patch.object(Path, "read_text", windows_read):
            g = Glossary(Path(td) / "glossary.json", {"宗主": "Sect Master"})
            g.save()
            restored = Glossary(g.path, {})
            self.assertEqual(restored.revision, g.revision)
            self.assertIn("宗主", restored.entries)

    def test_dub_voice_override_does_not_change_shared_cast(self):
        from anime_dubber.quality_pipeline import cast
        with tempfile.TemporaryDirectory() as td:
            cfg = Config("source", Path(td), voice_overrides={"s": {"tts_provider": "qwen", "qwen_speaker": "Vivian"}})
            path = Path(td) / "cast.json"
            profiles = cast([Segment(0, 1, "你好", speaker_id="s")], cfg, options(), path, CommandRunner())
            self.assertEqual(profiles["s"]["qwen_speaker"], "Vivian")
            shared = json.loads(path.read_text())["characters"][0]
            self.assertEqual(shared["qwen_speaker"], "Aiden")
            self.assertEqual(shared["tts_provider"], "inherit")

    def test_background_outside_dialogue_is_preserved_exactly(self):
        import numpy as np
        import soundfile as sf
        from anime_dubber.quality_pipeline import hybrid_background
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = np.full((48000 * 4, 2), .2, dtype="float32")
            separated = np.full_like(original, .05)
            sf.write(root / "original.wav", original, 48000, subtype="FLOAT")
            sf.write(root / "separated.wav", separated, 48000, subtype="FLOAT")
            result = hybrid_background(root / "original.wav", root / "separated.wav", [Segment(1, 2, "你好")], root, CommandRunner())
            bed, _ = sf.read(result, dtype="float32")
            np.testing.assert_array_equal(bed[:20000], original[:20000])
            np.testing.assert_array_equal(bed[150000:], original[150000:])
            np.testing.assert_allclose(bed[60000:80000], .05)

    def test_genuine_overlap_is_explicitly_blocked_not_silently_misattributed(self):
        from anime_dubber.quality_pipeline import assign_speakers
        with self.assertRaisesRegex(PipelineError, "Overlapping speakers"):
            assign_speakers([{"start": 0, "end": 1, "text": "你好"}],
                [{"start": 0, "end": 1, "speaker": "a"}, {"start": 0, "end": 1, "speaker": "b"}], [])

    @unittest.skipUnless(shutil.which("rubberband") and shutil.which("ffprobe"), "Rubber Band executable required")
    def test_rubberband_r3_actual_output_fits_and_preserves_duration_contract(self):
        import numpy as np
        import soundfile as sf
        from anime_dubber.quality_pipeline import fit_takes
        from anime_dubber.core import ffprobe_duration
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); audio = root / "take.wav"
            sr = 48000; t = np.arange(sr) / sr
            sf.write(audio, .1 * np.sin(2 * np.pi * 440 * t), sr, subtype="FLOAT")
            segments = [Segment(0, .85, "你好", "Hello", speaker_id="a")]
            q = options({"on_screen_borrow": 0}); q["media_duration"] = 1
            takes = [{"path": audio, "alignment": [{"start_time": .05, "end_time": .85}]}]
            clips, failed = fit_takes(segments, takes, [], q, root, CommandRunner())
            self.assertEqual(failed, [])
            self.assertLessEqual(ffprobe_duration(clips[0], CommandRunner()), .865)
            samples, sr = sf.read(clips[0])
            mono = samples.mean(axis=1)
            spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
            frequency = np.fft.rfftfreq(len(mono), 1 / sr)[np.argmax(spectrum)]
            self.assertLess(abs(frequency - 440), 3)
    def test_review_requires_real_source_evidence(self):
        bad = {"passed": False, "errors": [{"category": "negation", "source_evidence": "invented",
             "target_evidence": "", "explanation": "Missing negation"}]}
        with self.assertRaises(ValueError):
            validate_review(bad, "我没有背叛", "I betrayed them")
        with self.assertRaises(ValueError):
            validate_review({"passed": True, "errors": bad["errors"]}, "我没有背叛", "I betrayed them")

    def test_glossary_names_pronunciation_and_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            glossary = Glossary(Path(td) / "glossary.json", {"宗主": "Sect Master"})
            self.assertEqual(glossary.violations("宗主", "Elder"), ["宗主"])
            term = [{"source": "林风", "english": "Lin Feng", "kind": "name"}]
            glossary.learn(term, "林风来了", "Lin Feng is here", "cue-1")
            glossary.learn(term, "林风来了", "Lin Feng is here", "cue-2")
            glossary.entries["林风"]["pronunciation"] = "Lin Fung"
            self.assertEqual(glossary.voice_text("Lin Feng is here"), "Lin Fung is here")
            glossary.save()
            self.assertEqual(Glossary(glossary.path, {}).revision, glossary.revision)

    def test_every_cue_reviewed_and_models_never_overlap(self):
        calls, active = [], []
        class FakeWorker:
            def __init__(self, role, *_): self.role = role
            def __enter__(self):
                if active: raise AssertionError("Concurrent models")
                active.append(self.role); return self
            def __exit__(self, *_): active.clear()
            def call(self, request):
                calls.append(self.role)
                if self.role == "translator": return {"text": "Hello"}
                return {"text": json.dumps({"passed": True, "errors": [], "terms": []})}
        with tempfile.TemporaryDirectory() as td:
            cfg = Config("x", Path(td)); q = options()
            cp = Checkpoints(td, "source", cfg, CommandRunner())
            segs = [Segment(0, 1, "你好"), Segment(2, 3, "你好")]
            report, path = localize(segs, cfg, q, cp, Glossary(Path(td) / "g.json", {}),
                CommandRunner(), lambda _: None, lambda: None, worker_factory=FakeWorker)
            self.assertEqual(len(report), 2)
            # Identical evidence/context may reuse a reviewed text; every cue still has a result.
            self.assertTrue(json.loads(path.read_text())["complete"])
            self.assertIn("reviewer", calls)

    def test_incomplete_translation_saves_partial_and_never_runs_review(self):
        calls, partial = [], []
        class FakeWorker:
            def __init__(self, role, *_): calls.append(role)
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def call(self, request): return {"text": "你好"}
        with tempfile.TemporaryDirectory() as td:
            cfg = Config("x", Path(td))
            with self.assertRaisesRegex(PipelineError, "incomplete"):
                localize([Segment(0, 1, "你好")], cfg, options(), Checkpoints(td, "a", cfg, CommandRunner()),
                    Glossary(Path(td) / "g.json", {}), CommandRunner(), lambda _: None,
                    lambda: partial.append(True), worker_factory=FakeWorker)
            self.assertTrue(partial)
            self.assertNotIn("reviewer", calls)

class WorkerTests(unittest.TestCase):
    def test_real_rpc_process_exits_and_timeout_does_not_leak(self):
        real_popen = subprocess.Popen
        script = 'import sys,json,time\nfor line in sys.stdin:\n r=json.loads(line)["request"]\n time.sleep(r.get("delay",0))\n print("DUBCANVAS_RESULT "+json.dumps({"ok":True,"result":{"text":"ok"}}),flush=True)'
        def start(*args, **kwargs):
            return real_popen([sys.executable, "-u", "-c", script], **kwargs)
        settings = options({"mlx_python": sys.executable, "worker_timeout": 1})
        with patch("anime_dubber.quality_runtime.subprocess.Popen", side_effect=start):
            with Worker("asr", settings, CommandRunner()) as worker:
                self.assertEqual(worker.call({})["text"], "ok")
                proc = worker.proc
            self.assertIsNotNone(proc.poll())
            with self.assertRaisesRegex(PipelineError, "deadline"):
                with Worker("asr", settings, CommandRunner()) as worker:
                    proc = worker.proc
                    worker.call({"delay": 3})
            self.assertIsNotNone(proc.poll())

@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class QualityEndToEndTests(unittest.TestCase):
    def test_real_media_pipeline_with_contract_model_fixtures(self):
        # Real decoding/timing/mixing/muxing. Model outputs are fixtures, NOT model quality evidence.
        import numpy as np
        import soundfile as sf
        class FixtureWorker:
            def __init__(self, role, *_): self.role = role
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def call(self, r):
                if self.role == "separator":
                    audio, sr = sf.read(str(Path(r["input"]) / "audio.wav"), always_2d=True)
                    root = Path(r["output"]) / "audio"; root.mkdir(parents=True, exist_ok=True)
                    for name in ("speech", "music", "sfx"):
                        sf.write(str(root / (name + ".wav")), audio * (.5 if name == "speech" else .25), sr, subtype="FLOAT")
                    return {"output": r["output"]}
                if self.role == "diarizer": return {"tracks": [{"start": 0, "end": 4, "speaker": "speaker-0"}]}
                if self.role in {"asr", "verifier"}:
                    return {"text": "Hello" if r.get("language") == "English" else "你好"}
                if self.role == "aligner":
                    return {"items": [{"text": r["text"], "start_time": .05 if r["language"] == "English" else .5,
                        "end_time": .65 if r["language"] == "English" else 1.5}]}
                if self.role == "translator": return {"text": "Hello"}
                if self.role == "reviewer": return {"text": '{"passed":true,"errors":[],"terms":[]}'}
                if self.role == "tts":
                    t = np.arange(36000) / 48000
                    sf.write(r["output"], .2 * np.sin(2 * np.pi * 440 * t), 48000, subtype="FLOAT")
                    return {"output": r["output"]}
                raise AssertionError(self.role)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); video = root / "source.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=25:d=4",
                "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=4", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video)], check=True)
            cfg = Config(str(video), root / "out", pipeline_profile="quality_v1", source_language="zh", version_id="one", glossary={})
            from anime_dubber.core import source_key
            cfg.output_dir.mkdir()
            legacy_map = cfg.output_dir / f"{source_key(cfg.source, cfg.output_dir)}_characters.json"
            legacy_bytes = b'{"characters":[{"id":"old","display_name":"User edited voice","manual":true}]}'
            legacy_map.write_bytes(legacy_bytes)
            published = []
            runner = CommandRunner(); runner.artifact = lambda kind, *args: published.append(kind)
            with patch("anime_dubber.quality_pipeline.setup_issues", return_value=[]), \
                 patch("anime_dubber.quality_pipeline.runtime_fingerprints", return_value={"test": "contract fixtures"}), \
                 patch("anime_dubber.quality_pipeline.Worker", FixtureWorker), \
                 patch("anime_dubber.quality_runtime.Worker", FixtureWorker):
                result = run_pipeline(cfg, lambda _: None, runner)
                resumed = run_pipeline(cfg, lambda _: None, runner)
            self.assertTrue(result["dubbed_video"].is_file())
            self.assertTrue(resumed["dubbed_video"].is_file())
            self.assertEqual(legacy_map.read_bytes(), legacy_bytes)
            self.assertLess(published.index("translated_srt"), published.index("dubbed_video"))
            self.assertIn("Hello", result["translated_srt"].read_text())
            manifest = list((root / "out").rglob("run-manifest.json"))[0]
            self.assertTrue(json.loads(manifest.read_text())["complete"])
            self.assertFalse(json.loads(manifest.read_text())["hardware_verified"])

if __name__ == "__main__": unittest.main()
