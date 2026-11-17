from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anime_dubber.core import CommandRunner, Config, PipelineError, Segment, TimingOverlapError, run_pipeline
from anime_dubber.review import review_subtitles


def srt(texts):
    return "\n\n".join(
        f"{n}\n00:00:0{n-1},000 --> 00:00:0{n-1},500\n{text}"
        for n, text in enumerate(texts, 1)
    ) + "\n"


class ReviewTests(unittest.TestCase):
    def setUp(self):
        # Pipeline fixtures deliberately contain placeholder MP4 bytes. Decoder
        # behavior belongs to test_visual_sync, not subtitle review tests.
        visual = patch("anime_dubber.visual_sync.align_dub_to_visible_speech",
                       side_effect=lambda _video, segments, *_args, **_kwargs: list(segments))
        loudness = patch("anime_dubber.loudness.match_loudness", side_effect=lambda mixed, *_: mixed)
        loudness.start()
        self.addCleanup(loudness.stop)
        visual.start()
        self.addCleanup(visual.stop)


    def test_auto_rewrite_measures_voice_and_finishes_without_review(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            video = base / "source.mp4"; video.write_bytes(b"video")
            audio = base / "audio.wav"; audio.write_bytes(b"audio")
            cfg = Config(source=str(video), output_dir=base / "out", version_id="auto_fit",
                         translation="llm", multi_character=False)
            original = "The once famous Tang Sect"
            tested = []

            def prepare(seg, *_args, **_kwargs):
                tested.append(seg.translated)
                if seg.translated in (original, "The Tang Sect"):
                    raise TimingOverlapError(0, 3.2 if seg.translated == original else 3.0,
                                             2.8, seg.translated)
                return audio

            def transcribe(*_args, **_kwargs):
                return [Segment(0, .5, "唐门")]

            def translate(items, *_args):
                items[0].translated = original
                return items

            with patch("anime_dubber.core.download_source", return_value=video), \
                 patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(audio, audio)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=transcribe), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate), \
                 patch("anime_dubber.core.prepare_tts_clip", side_effect=prepare), \
                 patch("anime_dubber.timing.TimingRewriter.candidate", side_effect=["The Tang Sect", "Tang Sect"]) as candidate, \
                 patch("anime_dubber.core.ffprobe_duration", return_value=2.8), \
                 patch("anime_dubber.core.render_dub_timeline", return_value=audio), \
                 patch("anime_dubber.core.build_dialogue_safe_background", return_value=audio), \
                 patch("anime_dubber.core.mix_background_and_dub", return_value=audio), \
                 patch("anime_dubber.core.mux_video", side_effect=lambda _v, _a, dest, *_, **__: (dest.write_bytes(b"video"), dest)[1]):
                result = run_pipeline(cfg, lambda _: None, CommandRunner())
                resumed = run_pipeline(cfg, lambda _: None, CommandRunner())
            self.assertEqual(candidate.call_count, 2)
            self.assertEqual(tested, [original, "The Tang Sect", "Tang Sect", "Tang Sect"])
            self.assertIn("Tang Sect", result["translated_srt"].read_text(encoding="utf-8"))
            self.assertIn("Tang Sect", resumed["translated_srt"].read_text(encoding="utf-8"))
            fixes = json.loads(next((base / "out" / "versions" / "auto_fit").glob("*.timing-fixes.json")).read_text(encoding="utf-8"))
            self.assertEqual(fixes["1"]["replacement"], "Tang Sect")


    def test_timing_fallback_speeds_original_and_resumes_without_rewriting(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            video = base / "source.mp4"; video.write_bytes(b"video")
            audio = base / "audio.wav"; audio.write_bytes(b"audio")
            cfg = Config(source=str(video), output_dir=base / "out", version_id="tempo",
                         translation="llm", multi_character=False)
            calls = []

            def prepare(seg, *_args, **kwargs):
                calls.append((seg.translated, kwargs.get("max_tempo", 1.0)))
                if kwargs.get("max_tempo", 1.0) < 1.5:
                    raise TimingOverlapError(0, 3.2, 2.8, seg.translated)
                return audio

            def transcribe(*_args, **_kwargs):
                return [Segment(0, .5, "唐门")]

            def translate(items, *_args):
                items[0].translated = "The once famous Tang Sect"
                return items

            with patch("anime_dubber.core.download_source", return_value=video), \
                 patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(audio, audio)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=transcribe), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate), \
                 patch("anime_dubber.core.prepare_tts_clip", side_effect=prepare), \
                 patch("anime_dubber.timing.TimingRewriter.candidate", return_value=None) as candidate, \
                 patch("anime_dubber.core.ffprobe_duration", return_value=2.8), \
                 patch("anime_dubber.core.render_dub_timeline", return_value=audio), \
                 patch("anime_dubber.core.build_dialogue_safe_background", return_value=audio), \
                 patch("anime_dubber.core.mix_background_and_dub", return_value=audio), \
                 patch("anime_dubber.core.mux_video", side_effect=lambda _v, _a, dest, *_, **__: (dest.write_bytes(b"video"), dest)[1]):
                result = run_pipeline(cfg, lambda _: None, CommandRunner())
                run_pipeline(cfg, lambda _: None, CommandRunner())
            self.assertEqual(candidate.call_count, 0)
            self.assertEqual(calls, [("The once famous Tang Sect", 1.5),
                                     ("The once famous Tang Sect", 1.5)])
            self.assertIn("The once famous Tang Sect", result["translated_srt"].read_text(encoding="utf-8"))
            fixes = json.loads(next((base / "out" / "versions" / "tempo").glob("*.timing-fixes.json")).read_text(encoding="utf-8"))
            self.assertEqual(fixes["1"]["max_tempo"], 1.5)


    def test_unfittable_speech_finishes_with_review_flag_and_subtitles(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            video = base / "source.mp4"; video.write_bytes(b"video")
            audio = base / "audio.wav"; audio.write_bytes(b"audio")
            cfg = Config(source=str(video), output_dir=base / "out", version_id="dub_timing",
                         translation="llm", multi_character=False, review_before_dub=False)
            runner = CommandRunner()
            generated = []

            def transcribe(*_args, **_kwargs):
                return [Segment(0, 0.5, "你好")]

            def translate(items, *_args):
                items[0].translated = "A long greeting"
                return items

            def prepare(seg, *_args, **_kwargs):
                generated.append(seg.translated)
                if seg.translated == "A long greeting" and _kwargs.get("next_start") is not None:
                    raise TimingOverlapError(0, 1.5, 1.0, seg.translated)
                return audio

            warnings = []

            with patch("anime_dubber.core.download_source", return_value=video), \
                 patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(audio, audio)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=transcribe), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate), \
                 patch("anime_dubber.core.prepare_tts_clip", side_effect=prepare), \
                 patch("anime_dubber.timing.TimingRewriter.candidate", return_value=None), \
                 patch("anime_dubber.core.ffprobe_duration", return_value=1.0), \
                 patch("anime_dubber.core.render_dub_timeline", return_value=audio), \
                 patch("anime_dubber.core.build_dialogue_safe_background", return_value=audio), \
                 patch("anime_dubber.core.mix_background_and_dub", return_value=audio), \
                 patch("anime_dubber.core.mux_video", side_effect=lambda _v, _a, dest, *_, **__: (dest.write_bytes(b"video"), dest)[1]):
                result = run_pipeline(cfg, warnings.append, runner)
                self.assertEqual(json.loads(result["timing_report"].read_text())["1"]["status"], "needs_review")
                self.assertIn("A long greeting", result["translated_srt"].read_text())
            self.assertTrue(list((base / "out" / "versions" / "dub_timing").glob("*.srt")))
            self.assertTrue(list((base / "out" / "versions" / "dub_timing").glob("*DUB.mp4")))
            self.assertTrue(list((base / "out" / "versions" / "dub_timing").glob("*.timing.json")))


    def test_review_resume_skips_completed_model_suggestions(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            a, b, report = base / "zh.srt", base / "en.srt", base / "review.json"
            a.write_text(srt(["老六", "逼王"]), encoding="utf-8")
            b.write_text(srt(["Old Six", "Force the King"]), encoding="utf-8")
            class Tokenizer:
                chat_template = None
            calls = []
            def fake_generate(*args, **kwargs):
                calls.append(kwargs["prompt"])
                return '[{"id": %s, "text": "Better line"}]' % len(calls)
            with patch.dict("sys.modules", {"mlx_lm": type("FakeMLX", (), {
                "load": staticmethod(lambda _: (object(), Tokenizer())),
                "generate": staticmethod(fake_generate),
            })()}):
                one = review_subtitles(a, b, report, model="test", max_lines=1)
                self.assertEqual(one["flagged_cues"], 2)
                self.assertEqual(one["sampled_cues"], 1)
                two = review_subtitles(a, b, report, model="test", max_lines=1)
                self.assertEqual(len(calls), 1)
                self.assertEqual(two["flags"][0]["suggestion"], "Better line")
            # Pipeline's fast flagging pass must retain an earlier model review.
            three = review_subtitles(a, b, report)
            self.assertEqual(three["flags"][0]["suggestion"], "Better line")


    def test_review_retries_bad_model_format_using_alternate_mandarin(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            a, b, report, audio = (base / name for name in
                                   ("zh.srt", "en.srt", "en.review.json", "dialogue.wav"))
            a.write_text(srt(["behold"]), encoding="utf-8")
            b.write_text(srt(["Behold"]), encoding="utf-8")
            audio.write_bytes(b"audio")
            prompts = []

            def generate(*_args, **kwargs):
                prompts.append(kwargs["prompt"])
                return "unparseable model output" if len(prompts) == 2 else "You"

            with patch.dict(sys.modules, {
                "mlx_whisper": type("FakeASR", (), {
                    "transcribe": staticmethod(lambda *_a, **_k: {"text": "你"})})(),
                "mlx_lm": type("FakeLLM", (), {
                    "load": staticmethod(lambda _m: (object(), type("Tokenizer", (), {"chat_template": None})())),
                    "generate": staticmethod(generate)})(),
            }), patch("anime_dubber.review.subprocess.run"):
                result = review_subtitles(a, b, report, audio=audio, model="test")
            self.assertEqual(result["flags"][0]["asr_candidate"], "你")
            self.assertEqual(result["flags"][0]["asr_translation"], "You")
            self.assertEqual(result["flags"][0]["suggestion"], "You")
            self.assertNotIn("review_error", result["flags"][0])
            self.assertEqual(len(prompts), 3)
            self.assertIn("Mandarin: 你", prompts[0])
            self.assertNotIn("Behold", prompts[0])
            self.assertIn("source transcript is not Mandarin", prompts[1])
            self.assertIn("Mandarin transcription: 你", prompts[2])

            prompts.clear()
            def unchanged(*_args, **kwargs):
                prompts.append(kwargs["prompt"])
                return '[{"id": 1, "text": "Behold"}]' if len(prompts) == 1 else "You"
            with patch.dict(sys.modules, {
                "mlx_lm": type("FakeLLM", (), {
                    "load": staticmethod(lambda _m: (object(), type("Tokenizer", (), {"chat_template": None})())),
                    "generate": staticmethod(unchanged)})(),
            }):
                # Keep the alternate transcription cached; force a fresh suggestion.
                cached = json.loads(report.read_text(encoding="utf-8"))
                cached["flags"][0].pop("suggestion")
                report.write_text(json.dumps(cached), encoding="utf-8")
                revised = review_subtitles(a, b, report, model="test")
            self.assertEqual(revised["flags"][0]["suggestion"], "You")
            self.assertEqual(len(prompts), 2)

    def test_review_corrects_priority_cue_without_manual_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            video = base / "source.mp4"; video.write_bytes(b"video")
            audio = base / "audio.wav"; audio.write_bytes(b"audio")
            vocals = base / "vocals.wav"; vocals.write_bytes(b"vocals")
            background = base / "background.wav"; background.write_bytes(b"background")
            timeline = base / "timeline.wav"; timeline.write_bytes(b"timeline")
            mixed = base / "mixed.m4a"; mixed.write_bytes(b"mixed")
            clips = []
            config = Config(source=str(video), output_dir=base / "out", version_id="dub_review",
                            translation="llm", multi_character=False, review_before_dub=True)
            runner = CommandRunner()
            def model_review(command, **_kwargs):
                self.assertIn("review-subtitles", command)
                report = Path(command[command.index("--report") + 1])
                data = json.loads(report.read_text(encoding="utf-8"))
                data["flags"][0]["suggestion"] = "Behold"
                data["flags"][0]["asr_candidate"] = "你"
                data["flags"][0]["asr_translation"] = "You"
                report.write_text(json.dumps(data))
            runner.run = model_review
            def transcribe(*_args, **_kwargs):
                return [Segment(0, .5, "behold")]
            def translate(items, *_args):
                items[0].translated = "Behold"
                return items
            def prepare(seg, *_args, **_kwargs):
                clips.append(seg.translated)
                return audio
            with patch("anime_dubber.core.download_source", return_value=video), \
                 patch("anime_dubber.core.extract_audio", return_value=audio), \
                 patch("anime_dubber.core.separate_dialogue", return_value=(vocals, background)), \
                 patch("anime_dubber.core.transcribe_audio", side_effect=transcribe), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate), \
                 patch("anime_dubber.core.platform.system", return_value="Darwin"), \
                 patch("anime_dubber.core.platform.machine", return_value="arm64"), \
                 patch("anime_dubber.core.prepare_tts_clip", side_effect=prepare), \
                 patch("anime_dubber.core.ffprobe_duration", return_value=1.0), \
                 patch("anime_dubber.core.render_dub_timeline", return_value=timeline), \
                 patch("anime_dubber.core.build_dialogue_safe_background", return_value=background), \
                 patch("anime_dubber.core.mix_background_and_dub", return_value=mixed), \
                 patch("anime_dubber.core.mux_video", side_effect=lambda _v, _a, dest, *_, **__: (dest.write_bytes(b"video"), dest)[1]):
                result = run_pipeline(config, lambda _: None, runner)
                report = next((base / "out" / "versions" / "dub_review").glob("*.review.json"))
                data = json.loads(report.read_text(encoding="utf-8"))
                approval = report.with_name(report.name.replace(".review.json", ".review-approval.json"))
                saved = json.loads(approval.read_text(encoding="utf-8"))
                self.assertEqual(saved["signature"], data["signature"])
                self.assertEqual(saved["revisions"], {"1": "You"})
                runner.run = lambda *_args, **_kwargs: self.fail("Model must not run after approval")
                run_pipeline(config, lambda _: None, runner)
                self.assertIn("You", result["translated_srt"].read_text(encoding="utf-8"))
                approval.unlink()
                report.unlink()
                runner.run = lambda *_args, **_kwargs: (_ for _ in ()).throw(PipelineError("Model unavailable"))
                warning_messages = []
                fallback = run_pipeline(config, warning_messages.append, runner)
            self.assertEqual(clips, ["You", "You", "Behold"])
            self.assertIn("Behold", fallback["translated_srt"].read_text(encoding="utf-8"))
            self.assertTrue(any("stronger subtitle review failed" in msg for msg in warning_messages))


if __name__ == "__main__":
    unittest.main()
