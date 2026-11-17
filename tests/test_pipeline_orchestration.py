import tempfile
import unittest
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import numpy as np

from anime_dubber.core import (CommandRunner, Config, PipelineError, Segment, analyze_only,
                               run_pipeline)


class PipelineOrchestrationTests(unittest.TestCase):
    def setUp(self):
        # These tests fake source media and verify pipeline control flow.
        visual = patch("anime_dubber.visual_sync.align_dub_to_visible_speech",
                       side_effect=lambda _video, segments, *_args, **_kwargs: list(segments))
        visual.start()
        self.addCleanup(visual.stop)

    def test_untranslated_chinese_blocks_dub_before_subtitle_publish_or_tts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "video.mp4"; video.write_bytes(b"video")
            audio = root / "audio.wav"; audio.write_bytes(b"audio")
            cfg = Config(source=str(video), output_dir=root / "out", mode="dub",
                         source_language="zh", target_language="en", translation="llm")
            published = []
            runner = CommandRunner()
            runner.artifact = lambda kind, path, language: published.append(kind)
            with patch("anime_dubber.core.download_source", return_value=video), \
                 patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.transcribe_before_separation",
                       return_value=([Segment(30.6, 33.4, "天下武林,门派如林")], audio)), \
                 patch("anime_dubber.core.translate_with_llm",
                       side_effect=lambda rows, *_: [Segment(s.start, s.end, s.text, s.text) for s in rows]), \
                 patch("anime_dubber.core.separate_dialogue") as separation:
                with self.assertRaisesRegex(PipelineError, "translation to en is incomplete"):
                    run_pipeline(cfg, lambda _: None, runner)
            self.assertIn("chinese_srt", published)
            self.assertNotIn("translated_srt", published)
            separation.assert_not_called()


    def test_auto_detected_english_with_chinese_transcript_is_retranscribed_and_translated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); video = root / "video.mp4"; video.write_bytes(b"video")
            audio = root / "audio.wav"; audio.write_bytes(b"audio")
            config = Config(source=str(video), output_dir=root / "out", source_language="auto",
                            target_language="en", translation="llm", mode="subtitles")
            asr_calls = []
            def recognize(_audio, cfg, _work, _runner, _progress, *, task="transcribe"):
                asr_calls.append(cfg.source_language)
                cfg.source_language = "en" if cfg.source_language == "auto" else cfg.source_language
                return [Segment(0, 2, "天下武林门派如林")]
            def translate(rows, *_):
                return [Segment(s.start, s.end, s.text, "There are many martial arts sects") for s in rows]
            with patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(audio, audio)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=recognize), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate) as llm:
                result = run_pipeline(config, lambda _: None, CommandRunner())
            self.assertEqual(asr_calls, ["auto", "zh"])
            self.assertEqual(config.source_language, "zh")
            llm.assert_called_once()
            self.assertIn("There are many martial arts sects", result["translated_srt"].read_text())


    def test_dub_publishes_both_subtitle_sets_before_separation_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); source = root / "video.mp4"; source.write_bytes(b"source")
            audio = root / "audio.wav"; audio.write_bytes(b"audio")
            published = []
            runner = CommandRunner()
            runner.artifact = lambda kind, path, _language: published.append((kind, Path(path)))
            config = Config(source=str(source), output_dir=root / "out", source_language="es",
                            target_language="en", translation="llm", review_before_dub=False)

            def translate(rows, *_args):
                rows[0].translated = "Hello"
                return rows

            with patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.transcribe_audio", return_value=[Segment(0, 1, "Hola")]), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate), \
                 patch("anime_dubber.core.separate_dialogue", side_effect=PipelineError("Demucs failed")):
                with self.assertRaisesRegex(PipelineError, "Demucs failed"):
                    run_pipeline(config, lambda _message: None, runner)
            self.assertIn("source_srt", [kind for kind, _ in published])
            self.assertIn("translated_srt", [kind for kind, _ in published])
            self.assertTrue(next(path for kind, path in published if kind == "translated_srt").stat().st_size)

    def test_empty_direct_translation_fails_without_publishing_empty_subtitles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); source = root / "video.mp4"; source.write_bytes(b"source")
            audio = root / "audio.wav"; audio.write_bytes(b"audio")
            published = []
            runner = CommandRunner()
            runner.artifact = lambda kind, *_: published.append(kind)
            config = Config(source=str(source), output_dir=root / "out", source_language="zh",
                            target_language="en", translation="whisper", mode="subtitles")
            with patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(audio, audio)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=[[Segment(0, 1, "你好")], []]):
                with self.assertRaisesRegex(PipelineError, "Translation returned no usable dialogue"):
                    run_pipeline(config, lambda _message: None, runner)
            self.assertIn("chinese_srt", published)
            self.assertNotIn("translated_srt", published)

    def test_analysis_keeps_subtitles_when_speaker_analysis_fails_after_audio_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.mp4"; source.write_bytes(b"video")
            original = root / "original.wav"; original.write_bytes(b"soundtrack")
            separated = root / "vocals.wav"; separated.write_bytes(b"silent stem")
            published = []
            runner = CommandRunner()
            runner.artifact = lambda kind, path, language: published.append((kind, Path(path), language))
            config = Config(source=str(source), output_dir=root / "out", source_language="auto")

            def recognize(audio, cfg, *_args, **_kwargs):
                if audio == original:
                    cfg.source_language = "es"
                    return [Segment(0, 1, "Hola")]
                return []

            def fail_speaker_analysis(audio, *_args, **_kwargs):
                self.assertEqual(audio, original)
                self.assertEqual(published[1][0], "source_srt")
                self.assertTrue(published[1][1].is_file())
                raise RuntimeError("speaker analysis failed")

            with patch("anime_dubber.core.extract_audio", return_value=original), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(separated, original)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=recognize), \
                 patch("anime_dubber.characters.analyze_characters", side_effect=fail_speaker_analysis):
                with self.assertRaisesRegex(RuntimeError, "speaker analysis failed"):
                    analyze_only(config, lambda _message: None, runner)

            self.assertEqual([kind for kind, _, _ in published], ["source_video", "source_srt"])
            self.assertEqual(published[1][2], "es")
            self.assertIn("Hola", published[1][1].read_text())

    def test_detected_spanish_to_japanese_publishes_language_specific_subtitles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "clip.mp4"; source.write_bytes(b"video")
            audio = root / "dialog.wav"; audio.write_bytes(b"audio")
            published = []
            runner = CommandRunner()
            runner.artifact = lambda kind, path, language: published.append((kind, language))
            cfg = Config(source=str(source), output_dir=root / "out", source_language="auto",
                         target_language="ja", mode="subtitles", translation="llm")

            def recognize(_audio, config, *_args, **_kwargs):
                config.source_language = "es"
                return [Segment(0, 2, "Hola")]

            def translate(segments, *_args):
                segments[0].translated = "こんにちは"
                return segments

            with patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(audio, audio)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=recognize), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate):
                result = run_pipeline(cfg, lambda _message: None, runner)

            self.assertEqual(cfg.source_language, "es")
            self.assertIn(("source_srt", "es"), published)
            self.assertIn(("translated_srt", "ja"), published)
            self.assertIn("Hola", result["source_srt"].read_text())
            self.assertIn("こんにちは", result["translated_srt"].read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_auto_character_references_reach_chatterbox_in_full_dub(self):
        try:
            import soundfile as sf
        except ImportError:
            self.skipTest("soundfile required")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "source.mp4"
            video.write_bytes(b"video")
            vocals = root / "vocals.wav"
            rate = 16000
            def speech(freq):
                t = np.arange(rate * 3, dtype=np.float32) / rate
                return (.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
            sf.write(vocals, np.concatenate([speech(f) for f in (120, 280, 125, 275)]), rate)

            def transcribe(*_args, **_kwargs):
                return [Segment(i * 3, (i + 1) * 3, "甲" if i % 2 == 0 else "乙") for i in range(4)]

            def translate(items, *_args, **_kwargs):
                for item in items:
                    item.translated = "Hello"
                return items

            references = []
            def synthesize(_text, output, **kwargs):
                reference = Path(kwargs["reference_audio"])
                self.assertTrue(reference.is_file())
                references.append(reference)
                sf.write(output, speech(400), rate)

            cfg = Config(source=str(video), output_dir=root / "project", version_id="first_dub",
                         tts_engine="chatterbox", auto_voice_references=True, translation="llm", speaker_backend="acoustic",
                         speaker_threshold=.91, multi_character=True)
            with patch("anime_dubber.core.extract_audio", return_value=vocals), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(vocals, vocals)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=transcribe), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate), \
                 patch("anime_dubber.core.list_macos_voices", return_value=[]), \
                 patch("anime_dubber.providers.tts.synthesize_chatterbox", side_effect=synthesize), \
                 patch("anime_dubber.core.mux_video", side_effect=lambda _v, _a, dst, _r, _p: dst.write_bytes(b"final")):
                results = run_pipeline(cfg, lambda _m: None, CommandRunner())

            self.assertEqual(len(references), 4)
            self.assertEqual(references[0], references[2])
            self.assertEqual(references[1], references[3])
            self.assertNotEqual(references[0], references[1])
            self.assertTrue(results["dub_audio"].is_file())
            self.assertTrue(results["translated_srt"].is_file())
            self.assertTrue(results["dubbed_video"].is_file())
            character_map = json.loads(results["character_map"].read_text(encoding="utf-8"))
            self.assertEqual({Path(p["suggested_reference_audio"]) for p in character_map["characters"]}, set(references))


if __name__ == "__main__":
    unittest.main()
