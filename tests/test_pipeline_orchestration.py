import tempfile
import unittest
import json
import shutil
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from anime_dubber.characters import CharacterProfile
from anime_dubber.core import (CommandRunner, Config, PipelineError, Segment, analyze_only,
                               run_pipeline, transcribe_before_separation)


class PipelineOrchestrationTests(unittest.TestCase):
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
                with self.assertRaisesRegex(PipelineError, "Translation is incomplete"):
                    run_pipeline(cfg, lambda _: None, runner)
            self.assertIn("chinese_srt", published)
            self.assertNotIn("translated_srt", published)
            separation.assert_not_called()

    def test_separated_dialogue_is_transcribed_before_original_soundtrack(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            original = work / "original.wav"; vocals = work / "vocals.wav"
            config = Config(source="video.mp4", output_dir=work)
            with patch("anime_dubber.core.transcribe_audio", return_value=[Segment(0, 1, "Speech")]) as asr, \
                 patch("anime_dubber.core.separate_dialogue", return_value=(vocals, work / "bg.wav")):
                segments, transcript_audio = transcribe_before_separation(original, config, work, CommandRunner(), lambda _: None)
            self.assertEqual([call.args[0] for call in asr.call_args_list], [vocals])
            self.assertEqual(asr.call_args.args[2], work / "vocals_primary_asr_v1")
            self.assertEqual(segments[0].text, "Speech")
            self.assertEqual(transcript_audio, vocals)

    def test_empty_separated_dialogue_tries_original_without_old_cache(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            original = work / "original.wav"; vocals = work / "vocals.wav"
            config = Config(source="video.mp4", output_dir=work)
            with patch("anime_dubber.core.transcribe_audio",
                       side_effect=[[], [Segment(0, 1, "Recovered")]]) as asr, \
                 patch("anime_dubber.core.separate_dialogue", return_value=(vocals, work / "bg.wav")):
                segments, selected = transcribe_before_separation(original, config, work, CommandRunner(), lambda _: None)
            self.assertEqual([call.args[0] for call in asr.call_args_list], [vocals, original])
            self.assertEqual(asr.call_args.args[2], work / "vocals_primary_asr_v1" / "original_soundtrack_asr")
            self.assertEqual((segments[0].text, selected), ("Recovered", original))

    def test_old_mixed_soundtrack_transcript_is_not_reused(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "transcript_zh_mlx_whisper_v5_precise.json").write_text(
                json.dumps([Segment(0, 1, "Incorrect old transcript").to_dict()]))
            audio = work / "original.wav"; vocals = work / "vocals.wav"
            cfg = Config(source="video.mp4", output_dir=work, asr_provider="mlx_whisper")
            recognize = Mock(return_value={"language": "zh", "segments": [
                {"start": 0, "end": 1, "text": "正确的新对白"}]})
            with patch("anime_dubber.core.separate_dialogue", return_value=(vocals, work / "bg.wav")), \
                 patch.dict(sys.modules, {"mlx_whisper": types.SimpleNamespace(transcribe=recognize)}):
                segments, selected = transcribe_before_separation(audio, cfg, work, CommandRunner(), lambda _: None)
            recognize.assert_called_once()
            self.assertEqual((segments[0].text, selected), ("正确的新对白", vocals))

    def test_direct_translation_uses_the_same_separated_dialogue_stem(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); video = root / "video.mp4"; video.write_bytes(b"video")
            audio = root / "original.wav"; vocals = root / "vocals.wav"
            cfg = Config(source=str(video), output_dir=root / "out", mode="subtitles",
                         translation="whisper", source_language="es")
            calls = []
            def transcribe(path, _config, _work, _runner, _progress, *, task="transcribe"):
                calls.append((path, task))
                if path == audio:
                    return []
                return [Segment(0, 1, "Hola" if task == "transcribe" else "Hello")]
            with patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(vocals, root / "bg.wav")), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=transcribe):
                results = run_pipeline(cfg, lambda _: None, CommandRunner())
            self.assertEqual(calls, [(vocals, "transcribe"), (vocals, "translate")])
            self.assertIn("Hello", results["translated_srt"].read_text())

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
            self.assertIn("こんにちは", result["translated_srt"].read_text())

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
                         tts_engine="chatterbox", translation="llm", speaker_backend="acoustic",
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

    def test_multi_character_profiles_reach_tts(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            video = d / "video.mp4"; video.write_bytes(b"video")
            audio = d / "audio.wav"; audio.write_bytes(b"audio")
            vocals = d / "vocals.wav"; vocals.write_bytes(b"vocals")
            bg = d / "bg.wav"; bg.write_bytes(b"bg")
            timeline = d / "timeline.wav"; timeline.write_bytes(b"timeline")
            mixed = d / "mixed.m4a"; mixed.write_bytes(b"mixed")
            segs = [Segment(0, 1, "甲"), Segment(1, 2, "乙")]
            p1 = CharacterProfile(id="CHAR_001", display_name="Lead", role="lead", voice_class="male", macos_voice="Alex", tts_rate=200)
            p2 = CharacterProfile(id="CHAR_002", display_name="Side", role="supporting", voice_class="female", macos_voice="Samantha", tts_rate=210)

            def fake_translate(items, *_args, **_kwargs):
                items[0].translated = "One"; items[1].translated = "Two"; return items

            def fake_analyze(speaker_audio, items, *_args, **_kwargs):
                self.assertEqual(speaker_audio, audio)
                items[0].speaker_id = "CHAR_001"; items[0].style = "shouting"
                items[1].speaker_id = "CHAR_002"; items[1].style = "whispering"
                payload = {"version": 3, "characters": [p1.to_dict(), p2.to_dict()], "segments": []}
                return [p1, p2], payload

            seen = []
            published = []
            runner = CommandRunner()
            runner.artifact = lambda kind, path, language: published.append((kind, Path(path).exists()))
            def fake_tts(seg, index, tts_dir, config, runner, progress, profile=None, next_start=None):
                self.assertIn(("translated_srt", True), published)
                seen.append((seg.speaker_id, seg.style, (profile or {}).get("macos_voice")))
                out = d / f"clip{index}.wav"; out.write_bytes(b"clip"); return out

            cfg = Config(source=str(video), output_dir=d / "out", mode="dub", translation="llm", multi_character=True, series_id="series")
            with patch("anime_dubber.core.download_source", return_value=video), \
                 patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(vocals, bg)), \
                 patch("anime_dubber.core.transcribe_audio", return_value=segs), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=fake_translate), \
                 patch("anime_dubber.core.list_macos_voices", return_value=["Alex", "Samantha"]), \
                 patch("anime_dubber.characters.analyze_characters", side_effect=fake_analyze), \
                 patch("anime_dubber.core.prepare_tts_clip", side_effect=fake_tts), \
                 patch("anime_dubber.core.ffprobe_duration", return_value=2.0), \
                 patch("anime_dubber.core.render_dub_timeline", return_value=timeline), \
                 patch("anime_dubber.core.build_dialogue_safe_background", return_value=bg), \
                 patch("anime_dubber.core.mix_background_and_dub", return_value=mixed), \
                 patch("anime_dubber.core.mux_video", side_effect=lambda _v, _a, f, _r, _p: f.write_bytes(b"final")):
                results = run_pipeline(cfg, lambda _m: None, runner)

            self.assertEqual(seen, [
                ("CHAR_001", "shouting", "Alex"),
                ("CHAR_002", "whispering", "Samantha"),
            ])
            self.assertTrue(results["dubbed_video"].exists())
            self.assertTrue(results["character_map"].exists())


if __name__ == "__main__":
    unittest.main()
