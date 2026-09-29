import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from anime_dubber.core import (
    CommandRunner, Config, Segment, TranscriptionQualityError, run_pipeline,
    transcribe_audio, transcribe_source_audio, validate_transcript_content,
)


def credit_rows():
    return [{"start": start, "end": start + 1, "text": "© BF-WATCH TV 2021"}
            for start in (28.34, 42.32, 73.78, 100.58)]


class TranscriptionQualityTests(unittest.TestCase):
    def test_suspicious_stem_retries_original_and_redetects_language(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Config(source="video.mp4", output_dir=root, source_language="auto",
                         asr_provider="faster_whisper")
            calls = []
            def recognize(audio, **kwargs):
                calls.append(audio.name)
                self.assertIsNone(kwargs["language"])
                kwargs["language_sink"]("en" if audio.name == "vocals.wav" else "zh")
                return credit_rows() if audio.name == "vocals.wav" else [
                    {"start": 30.6, "end": 33.4, "text": "天下武林，门派如林"}]
            with patch("anime_dubber.providers.asr.faster_whisper_segments", side_effect=recognize):
                rows, audio = transcribe_source_audio(root / "vocals.wav", root / "original.wav",
                    cfg, root, CommandRunner(), lambda _: None)
            self.assertEqual(calls, ["vocals.wav", "original.wav"])
            self.assertEqual(cfg.source_language, "zh")
            self.assertEqual(audio.name, "original.wav")
            self.assertEqual(rows[0].text, "天下武林，门派如林")
            self.assertFalse((root / "detected_source_language.json").exists())
            self.assertFalse((root / "transcript_auto_faster_whisper_v5_precise.json").exists())

    def test_bad_cached_transcript_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "transcript_zh_faster_whisper_v5_precise.json"
            cache.write_text(json.dumps(credit_rows()))
            cfg = Config(source="video.mp4", output_dir=root, source_language="zh",
                         asr_provider="faster_whisper")
            with patch("anime_dubber.providers.asr.faster_whisper_segments", return_value=[
                    {"start": 30.6, "end": 33.4, "text": "天下武林，门派如林"}]) as recognize:
                rows = transcribe_audio(root / "audio.wav", cfg, root, CommandRunner(), lambda _: None)
            recognize.assert_called_once()
            self.assertEqual(rows[0].text, "天下武林，门派如林")
            self.assertNotIn("BF-WATCH", cache.read_text())

    def test_mlx_credit_hallucination_is_not_cached_or_used_for_language(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Config(source="video.mp4", output_dir=root, source_language="auto",
                         asr_provider="mlx_whisper")
            recognize = Mock(return_value={"language": "en", "segments": credit_rows()})
            with patch.dict(sys.modules, {"mlx_whisper": types.SimpleNamespace(transcribe=recognize)}):
                with self.assertRaisesRegex(TranscriptionQualityError, "repeated copyright"):
                    transcribe_audio(root / "audio.wav", cfg, root, CommandRunner(), lambda _: None)
            self.assertEqual(cfg.source_language, "auto")
            self.assertEqual(list(root.glob("*.json")), [])

    def test_two_bad_tracks_block_subtitle_publication_and_tts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "video.mp4"; video.write_bytes(b"video")
            audio = root / "audio.wav"; vocals = root / "vocals.wav"
            cfg = Config(source=str(video), output_dir=root / "out", source_language="zh",
                         asr_provider="faster_whisper", mode="dub")
            published = []
            runner = CommandRunner()
            runner.artifact = lambda kind, _path, _language: published.append(kind)
            with patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(vocals, root / "bg.wav")), \
                 patch("anime_dubber.providers.asr.faster_whisper_segments", return_value=credit_rows()) as asr, \
                 patch("anime_dubber.core.prepare_tts_clip") as tts:
                with self.assertRaisesRegex(TranscriptionQualityError, "repeated copyright"):
                    run_pipeline(cfg, lambda _: None, runner)
            self.assertEqual(asr.call_count, 2)
            self.assertFalse(any(kind.endswith(("_srt", "_vtt")) for kind in published))
            self.assertEqual(list((root / "out").rglob("*.srt")), [])
            tts.assert_not_called()

    def test_repeated_real_dialogue_and_incidental_credits_are_preserved(self):
        dialogue = [Segment(i * 2, i * 2 + 1, "Come on!") for i in range(8)]
        validate_transcript_content(dialogue)
        validate_transcript_content(dialogue + [Segment(20, 21, "© BF-WATCH TV 2021")])
        validate_transcript_content([Segment(i, i + 0.5, "© BF-WATCH TV 2021") for i in range(4)] + dialogue)


if __name__ == "__main__":
    unittest.main()
