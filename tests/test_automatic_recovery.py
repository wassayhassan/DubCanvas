from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anime_dubber.core import Config, CommandRunner, Segment, PipelineError, CancelledError
from anime_dubber.localization import Glossary
from anime_dubber.quality_models import options
from anime_dubber.quality_pipeline import generate_checked_speech, SpeechValidationFailure, speech_qa, synthesize_takes
from anime_dubber.quality_runtime import Checkpoints

class AutomaticRecoveryTests(unittest.TestCase):
    def test_pause_during_synthesis_is_not_a_voice_failure(self):
        class PausedWorker:
            def __init__(self, *_): pass
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def call(self, request): raise CancelledError("Paused by user")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = Config("source", root); runner = CommandRunner()
            cp = Checkpoints(root / "cp", "source", cfg, runner)
            with patch("anime_dubber.quality_pipeline.Worker", PausedWorker), self.assertRaises(CancelledError):
                synthesize_takes([Segment(0, 1, "甲", "Hello", "a")],
                    {"a": {"tts_provider": "qwen", "kokoro_voice": "am_michael"}}, Glossary(root / "g.json", {}),
                    cfg, options(), root, cp, runner, lambda _: None)

    def test_repair_retries_only_failed_cue_without_manual_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = Config("source", root); runner = CommandRunner()
            cp = Checkpoints(root / "cp", "source", cfg, runner)
            segs = [Segment(0, 1, "甲", "Hello", "a"), Segment(2, 3, "乙", "Goodbye", "b")]
            attempts = []
            def synthesize(*args, **kwargs):
                attempts.append(dict(kwargs["attempts"])); return [{}, {}]
            with patch("anime_dubber.quality_pipeline.synthesize_takes", side_effect=synthesize), \
                 patch("anime_dubber.quality_pipeline.speech_qa", side_effect=[SpeechValidationFailure({1: "missing word"}), None]):
                generate_checked_speech(segs, {}, Glossary(root / "g.json", {}), cfg, options(), root, cp, runner, lambda _: None)
            self.assertEqual(attempts, [{}, {1: 1}])

    def test_bad_speech_recovers_with_character_wide_fallback_and_preserves_shared_cast(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = Config("source", root); runner = CommandRunner()
            cp = Checkpoints(root / "cp", "source", cfg, runner)
            segs = [Segment(0, 1, "甲", "Hello", "a"), Segment(2, 3, "乙", "Goodbye", "a")]
            shared = {"a": {"tts_provider": "qwen", "kokoro_voice": "am_michael"}}
            profiles = {sid: dict(row) for sid, row in shared.items()}
            with patch("anime_dubber.quality_pipeline.synthesize_takes", return_value=[{}, {}]), \
                 patch("anime_dubber.quality_pipeline.speech_qa", side_effect=[SpeechValidationFailure({0: "bad word"})] * 3 + [None]):
                generate_checked_speech(segs, profiles, Glossary(root / "g.json", {}), cfg, options(), root, cp, runner, lambda _: None)
            self.assertEqual(profiles["a"]["tts_provider"], "kokoro")
            self.assertEqual(shared["a"]["tts_provider"], "qwen")

    def test_invalid_synthesis_is_retried_and_resource_failures_are_not_looped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cfg = Config("source", root); runner = CommandRunner()
            cp = Checkpoints(root / "cp", "source", cfg, runner)
            args = ([Segment(0, 1, "甲", "Hello", "a")], {}, Glossary(root / "g.json", {}), cfg, options(), root, cp, runner, lambda _: None)
            with patch("anime_dubber.quality_pipeline.synthesize_takes", side_effect=[SpeechValidationFailure({0: "no audio"}), [{}]]) as synth, \
                 patch("anime_dubber.quality_pipeline.speech_qa"):
                generate_checked_speech(*args)
                self.assertEqual(synth.call_count, 2)
            with patch("anime_dubber.quality_pipeline.synthesize_takes", side_effect=PipelineError("memory budget exceeded")) as synth:
                with self.assertRaisesRegex(PipelineError, "memory"):
                    generate_checked_speech(*args)
                self.assertEqual(synth.call_count, 1)

    def test_recognizer_disagreement_uses_independent_acoustic_check(self):
        roles = []
        class FakeWorker:
            def __init__(self, role, *_): self.role = role; roles.append(role)
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def call(self, request):
                if self.role == "verifier": return {"text": "I did win"}
                if self.role == "fallback":
                    assert request["language"] == "English"
                    return {"text": "I did not win"}
                return {"items": [{"start_time": 0, "end_time": 1}]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); audio = root / "take.wav"; audio.write_bytes(b"fixture")
            cfg = Config("source", root); runner = CommandRunner()
            cp = Checkpoints(root / "cp", "source", cfg, runner)
            take = {"path": audio, "text": "I did not win", "identity": {"cue": 1}}
            with patch("anime_dubber.quality_pipeline.Worker", FakeWorker):
                speech_qa([take], options(), cp, runner, lambda _: None)
            self.assertEqual(roles, ["verifier", "fallback", "aligner"])
            self.assertEqual(take["recognized"], "I did not win")

    def test_missing_word_alignment_uses_whole_verified_take(self):
        class FakeWorker:
            def __init__(self, role, *_): self.role = role
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def call(self, request):
                return {"text": "Hello"} if self.role == "verifier" else {"items": []}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); audio = root / "take.wav"; audio.write_bytes(b"fixture")
            cfg = Config("source", root); runner = CommandRunner()
            cp = Checkpoints(root / "cp", "source", cfg, runner)
            take = {"path": audio, "text": "Hello", "identity": {"cue": 1}}
            with patch("anime_dubber.quality_pipeline.Worker", FakeWorker), \
                 patch("anime_dubber.core.ffprobe_duration", return_value=1.2):
                speech_qa([take], options(), cp, runner, lambda _: None)
            self.assertEqual(take["alignment"], [{"start_time": 0.0, "end_time": 1.2, "text": "Hello"}])

if __name__ == "__main__": unittest.main()
