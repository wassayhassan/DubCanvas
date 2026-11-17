import shutil
import subprocess
import tempfile
import unittest
import wave
import json
from array import array
from math import pi, sin, sqrt
from unittest.mock import patch
from pathlib import Path

from anime_dubber.core import (
    CommandRunner,
    Config,
    PipelineError,
    Segment,
    TimingOverlapError,
    ffprobe_duration,
    build_dialogue_safe_background,
    render_dub_timeline,
    prepare_tts_clip,
    _complete_wav,
    extract_audio,
    separate_dialogue,
    validate_source_cache,
    run_pipeline,
)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class FfmpegPipelineTests(unittest.TestCase):
    def test_demucs_success_with_short_stems_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); audio = root / "original.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(2); output.setsampwidth(2); output.setframerate(44100)
                output.writeframes(b"\0" * 44100 * 4)
            runner = CommandRunner()
            real_run = runner.run
            def incomplete_demucs(cmd, **kwargs):
                if cmd[0] == "ffprobe":
                    return real_run(cmd, **kwargs)
                stem_dir = root / "stems" / "htdemucs" / "original"
                stem_dir.mkdir(parents=True, exist_ok=True)
                for name in ("vocals.wav", "no_vocals.wav"):
                    with wave.open(str(stem_dir / name), "wb") as output:
                        output.setnchannels(2); output.setsampwidth(2); output.setframerate(44100)
                        output.writeframes(b"\0" * 4410 * 4)
                return subprocess.CompletedProcess([], 0, "", "")
            with patch.object(runner, "run", side_effect=incomplete_demucs):
                with self.assertRaisesRegex(PipelineError, "incomplete audio stems"):
                    separate_dialogue(audio, root, runner, lambda _: None,
                                      Config(source="video.mp4", output_dir=root, demucs_device="cpu"))

    def test_full_media_path_creates_playable_video_with_audible_dub_and_subtitles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); source = root / "video.mp4"; voice = root / "voice.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=160x120:d=2.4", "-f", "lavfi", "-i",
                            "anullsrc=r=44100:cl=stereo", "-t", "2.4", "-c:v", "mpeg4",
                            "-c:a", "aac", str(source)], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=880:duration=0.5", "-ac", "2", "-ar", "44100",
                            str(voice)], check=True)
            config = Config(source=str(source), output_dir=root / "out", source_language="es",
                            target_language="en", translation="llm", multi_character=False,
                            review_before_dub=False, chunk_seconds=30)

            def translate(segments, *_args):
                segments[0].translated = "Hello"
                return segments

            with patch("anime_dubber.core.transcribe_audio", return_value=[Segment(0.4, 1.1, "Hola")]), \
                 patch("anime_dubber.core.translate_with_llm", side_effect=translate), \
                 patch("anime_dubber.core.separate_dialogue", side_effect=lambda audio, *_args: (audio, audio)), \
                 patch("anime_dubber.core.prepare_tts_clip", return_value=voice):
                result = run_pipeline(config, lambda _: None, CommandRunner())
            self.assertIn("Hola", result["source_srt"].read_text())
            self.assertIn("Hello", result["translated_srt"].read_text())
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                    "stream=codec_type:format=duration", "-of", "json",
                                    str(result["dubbed_video"])], capture_output=True, text=True, check=True)
            media = json.loads(probe.stdout)
            self.assertEqual({s["codec_type"] for s in media["streams"]}, {"video", "audio"})
            self.assertGreater(float(media["format"]["duration"]), 2.2)
            output_wav = root / "check.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i",
                            str(result["dubbed_video"]), "-vn", "-c:a", "pcm_s16le", str(output_wav)], check=True)
            with wave.open(str(output_wav), "rb") as handle:
                samples = array("h")
                samples.frombytes(handle.readframes(handle.getnframes()))
            self.assertGreater(sqrt(sum(value * value for value in samples) / len(samples)), 100.0)

    def test_replaced_source_invalidates_extracted_audio(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); source = root / "same-name.mp4"; work = root / "work"; work.mkdir()
            runner = CommandRunner()
            config = Config(source=str(source), output_dir=root / "out")
            durations = []
            for seconds in (1, 2):
                subprocess.run([
                    "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=black:s=160x120:d={seconds}", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", "-shortest", "-c:v", "mpeg4",
                    "-c:a", "aac", str(source),
                ], check=True)
                validate_source_cache(source, work, lambda _message: None)
                durations.append(ffprobe_duration(extract_audio(source, work, runner, lambda _: None, config), runner))
            self.assertLess(durations[0], 1.2)
            self.assertGreater(durations[1], 1.9)

    def test_video_without_audio_is_rejected_before_transcription(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); source = root / "silent.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=160x120:d=1", "-c:v", "mpeg4", str(source)], check=True)
            with self.assertRaisesRegex(PipelineError, "no decodable audio stream"):
                extract_audio(source, root, CommandRunner(), lambda _: None,
                              Config(source=str(source), output_dir=root))

    def test_truncated_cached_wav_is_not_a_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "interrupted.wav"
            with wave.open(str(wav_path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44100)
                output.writeframes(b"\0" * (44100 * 4))
            self.assertTrue(_complete_wav(wav_path, minimum_seconds=.98))
            wav_path.write_bytes(wav_path.read_bytes()[:10000])
            self.assertFalse(_complete_wav(wav_path, minimum_seconds=.98))


    def test_short_gap_between_voices_does_not_restore_original_speech(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            original, separated = d / "original.wav", d / "no_vocals.wav"
            for path, freq in ((original, 220), (separated, 440)):
                subprocess.run([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=4",
                    "-ac", "2", "-ar", "44100", str(path),
                ], check=True)
            bed = build_dialogue_safe_background(
                original, separated,
                [Segment(1.0, 1.2, "female"), Segment(2.7, 2.9, "male")],
                4.0, d, Config(source="x", output_dir=d), CommandRunner(), lambda _: None,
            )
            with wave.open(str(bed), "rb") as output:
                samples = array("h")
                samples.frombytes(output.readframes(output.getnframes()))
                mono = samples[::output.getnchannels()]
                rate = output.getframerate()
            between = mono[int(1.9 * rate):int(2.0 * rate)]
            original_tone = abs(sum(v * sin(2 * pi * 220 * i / rate) for i, v in enumerate(between)))
            separated_tone = abs(sum(v * sin(2 * pi * 440 * i / rate) for i, v in enumerate(between)))
            self.assertGreater(separated_tone, original_tone * 5)


    def test_timeline_works_in_apostrophe_path(self):
        with tempfile.TemporaryDirectory(prefix="anime_dubber_O'Brien_") as td:
            d = Path(td)
            clip = d / "clip.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.25",
                "-ac", "2", "-ar", "44100", str(clip),
            ], check=True)
            segs = [Segment(0.2, 0.45, "a", "A")]
            cfg = Config(source="x", output_dir=d, chunk_seconds=30, ducking=False)
            runner = CommandRunner()
            timeline = render_dub_timeline(segs, [clip], 1.0, d, cfg, runner, lambda _m: None)
            self.assertTrue(timeline.exists())
            self.assertGreaterEqual(ffprobe_duration(timeline, runner), 0.99)

    def test_tts_keeps_natural_speech_in_gap_and_stops_before_next_voice(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            long_source = d / "long.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
                "-ac", "1", "-ar", "44100", str(long_source),
            ], check=True)

            def fake_say(_text, out_aiff, _config, _runner, voice="", rate=205):
                shutil.copyfile(long_source, out_aiff)

            cfg = Config(source="x", output_dir=d, tts_engine="macos", force=True)
            seg = Segment(5.0, 5.40, "你好", "Hello")
            runner = CommandRunner()
            messages = []
            with patch("anime_dubber.core.synthesize_macos", side_effect=fake_say), \
                 patch.object(runner, "run", wraps=runner.run) as commands:
                clip = prepare_tts_clip(seg, 0, d / "tts", cfg, runner, messages.append,
                                        next_start=7.2)

            duration = ffprobe_duration(clip, runner)
            self.assertTrue(1.9 <= duration <= 2.05)
            self.assertTrue(any("naturally" in message for message in messages))
            filters = [args[0][args[0].index("-af") + 1] for args, _ in
                       ((call.args, call.kwargs) for call in commands.call_args_list)
                       if "-af" in args[0]]
            self.assertFalse(any("atempo=" in item or "atrim=duration=" in item for item in filters))

            with patch("anime_dubber.core.synthesize_macos", side_effect=fake_say):
                with self.assertRaises(TimingOverlapError):
                    prepare_tts_clip(seg, 1, d / "tts", cfg, CommandRunner(), messages.append,
                                     next_start=5.8)

            with patch("anime_dubber.core.synthesize_macos", side_effect=fake_say), \
                 patch.object(runner, "run", wraps=runner.run) as sped_commands:
                sped_clip = prepare_tts_clip(seg, 2, d / "tts", cfg, runner, messages.append,
                                             next_start=6.4, max_tempo=1.5)
            self.assertLessEqual(ffprobe_duration(sped_clip, runner), 1.43)
            speed_filters = [call.args[0][call.args[0].index("-af") + 1]
                             for call in sped_commands.call_args_list if "-af" in call.args[0]]
            self.assertTrue(any("atempo=" in item for item in speed_filters))
            self.assertFalse(any("atempo=1.5" in item for item in speed_filters))

            with patch("anime_dubber.core.synthesize_macos", side_effect=fake_say):
                with self.assertRaises(TimingOverlapError):
                    prepare_tts_clip(seg, 3, d / "tts", cfg, CommandRunner(), messages.append,
                                     next_start=6.0, max_tempo=1.5)

            with patch("anime_dubber.core.synthesize_macos", side_effect=fake_say):
                fallback = prepare_tts_clip(seg, 4, d / "tts", cfg, runner, messages.append,
                                            next_start=None, max_tempo=1.5)
            self.assertLessEqual(ffprobe_duration(fallback, runner), 1.4)


    def test_trailing_tts_silence_does_not_force_fast_speech(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            padded = d / "padded.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                "-af", "apad=pad_dur=2", "-ar", "44100", str(padded),
            ], check=True)

            def fake_say(_text, path, _config, _runner, voice="", rate=205):
                shutil.copyfile(padded, path)

            cfg = Config(source="x", output_dir=d, tts_engine="macos")
            warnings = []
            with patch("anime_dubber.core.synthesize_macos", side_effect=fake_say):
                clip = prepare_tts_clip(Segment(0, 0.5, "你好", "Hello"), 0, d / "tts",
                                        cfg, CommandRunner(), warnings.append)
            self.assertTrue(clip.exists())
            self.assertFalse(any("speech compression" in message for message in warnings))


if __name__ == "__main__":
    unittest.main()
