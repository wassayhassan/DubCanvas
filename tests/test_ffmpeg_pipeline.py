import platform
import shutil
import subprocess
import tempfile
import unittest
import wave
import json
from array import array
from math import sqrt
from unittest.mock import patch
from pathlib import Path

from anime_dubber.core import (
    CommandRunner,
    Config,
    PipelineError,
    Segment,
    TimingOverlapError,
    ffprobe_duration,
    mix_background_and_dub,
    mux_video,
    build_dialogue_safe_background,
    render_dub_timeline,
    prepare_tts_clip,
    synthesize_macos,
    _pitch_filters,
    _ffconcat_quote,
    _complete_wav,
    extract_audio,
    validate_source_cache,
    run_pipeline,
)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class FfmpegPipelineTests(unittest.TestCase):
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

    def test_local_wav_tts_renders_to_distinct_output_and_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            raw = directory / "raw.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
                "-ac", "1", "-ar", "44100", str(raw),
            ], check=True)

            cfg = Config(source="x", output_dir=directory, tts_engine="chatterbox")
            segment = Segment(0.0, 0.5, "你好", "Hello")
            runner = CommandRunner()
            with patch("anime_dubber.providers.tts.synthesize_chatterbox", side_effect=lambda _text, path, **_kw: shutil.copyfile(raw, path)) as synthesize:
                first = prepare_tts_clip(segment, 0, directory / "tts", cfg, runner, lambda _m: None)
                second = prepare_tts_clip(segment, 0, directory / "tts", cfg, runner, lambda _m: None)

            self.assertEqual(first, second)
            self.assertIn("_processed.wav", first.name)
            self.assertEqual(synthesize.call_count, 1)
            self.assertGreater(ffprobe_duration(first, runner), 0.1)
            self.assertTrue(1.9 <= ffprobe_duration(first, runner) <= 2.05)
            self.assertFalse(any((directory / "tts").glob("*_rendering.wav")))

    def test_timeline_and_music_sfx_mix_keep_duration(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=31",
                "-ac", "2", "-ar", "44100", str(d / "bg.wav"),
            ], check=True)
            clips = []
            for i, freq in enumerate((880, 660)):
                p = d / f"clip_{i}.wav"
                subprocess.run([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=0.4",
                    "-ac", "2", "-ar", "44100", str(p),
                ], check=True)
                clips.append(p)
            segs = [Segment(0.5, 0.9, "a", "A"), Segment(30.2, 30.6, "b", "B")]
            cfg = Config(source="x", output_dir=d, chunk_seconds=30, ducking=True)
            runner = CommandRunner()
            timeline = render_dub_timeline(segs, clips, 31.0, d, cfg, runner, lambda _m: None)
            mixed = mix_background_and_dub(d / "bg.wav", timeline, d, cfg, runner, lambda _m: None)
            self.assertGreaterEqual(ffprobe_duration(timeline, runner), 30.99)
            self.assertGreaterEqual(ffprobe_duration(mixed, runner), 30.99)

    def test_character_cloning_uses_own_reference_and_falls_back_to_character_voice(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            raw = d / "speech.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                "-ac", "1", "-ar", "44100", str(raw),
            ], check=True)
            cfg = Config(source="x", output_dir=d, tts_engine="chatterbox", multi_character=True,
                         chatterbox_reference_audio="/global-female.wav")
            seg = Segment(0, 1, "你好", "Hello", speaker_id="CHAR_001")
            reference = d / "character.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=330:duration=6",
                "-ac", "1", "-ar", "16000", str(reference),
            ], check=True)
            with patch("anime_dubber.providers.tts.synthesize_chatterbox",
                       side_effect=lambda _text, path, **_kw: shutil.copyfile(raw, path)) as clone:
                prepare_tts_clip(seg, 0, d / "tts", cfg, CommandRunner(), lambda _m: None,
                                 profile={"id": "CHAR_001", "suggested_reference_audio": str(reference)})
            self.assertEqual(clone.call_args.kwargs["reference_audio"], str(reference))
            self.assertTrue(clone.call_args.kwargs["american_english"])

            seg.speaker_id = "CHAR_002"
            with patch("anime_dubber.providers.tts.kokoro_available", return_value=True), \
                 patch("anime_dubber.providers.tts.synthesize_kokoro",
                       side_effect=lambda _text, path, **_kw: shutil.copyfile(raw, path)) as preset:
                prepare_tts_clip(seg, 1, d / "tts", cfg, CommandRunner(), lambda _m: None,
                                 profile={"id": "CHAR_002", "suggested_reference_audio": str(raw),
                                          "voice_class": "female", "age_group": "child"})
            self.assertEqual(preset.call_args.kwargs["voice"], "af_heart")


    def test_dialogue_safe_background_keeps_duration(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            original = d / "original.wav"
            separated = d / "no_vocals.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
                "-ac", "2", "-ar", "44100", str(original),
            ], check=True)
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-ac", "2", "-ar", "44100", str(separated),
            ], check=True)
            cfg = Config(source="x", output_dir=d, chunk_seconds=30, ducking=False)
            runner = CommandRunner()
            bed = build_dialogue_safe_background(
                original, separated, [Segment(1.0, 1.5, "hello")], 3.0, d, cfg, runner, lambda _m: None
            )
            self.assertTrue(bed.exists())
            self.assertGreaterEqual(ffprobe_duration(bed, runner), 2.99)

    def test_ffconcat_quote_handles_apostrophe(self):
        quoted = _ffconcat_quote(Path("/tmp/O'Brien/chunk.wav"))
        self.assertEqual(quoted, "'/tmp/O'\\''Brien/chunk.wav'")

    def test_timeline_places_voice_at_segment_start_with_ms_precision(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            clip = d / "sync_clip.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.20",
                "-ac", "2", "-ar", "44100", str(clip),
            ], check=True)

            seg = Segment(1.234, 1.434, "a", "A")
            cfg = Config(source="x", output_dir=d, chunk_seconds=30, force=True)
            runner = CommandRunner()
            timeline = render_dub_timeline([seg], [clip], 2.0, d, cfg, runner, lambda _m: None)

            with wave.open(str(timeline), "rb") as wav:
                rate = wav.getframerate()
                channels = wav.getnchannels()
                width = wav.getsampwidth()
                self.assertEqual(width, 2)
                frames = wav.readframes(wav.getnframes())

            import array
            samples = array.array("h")
            samples.frombytes(frames)
            first_frame = None
            threshold = 100
            for frame_index in range(len(samples) // channels):
                base = frame_index * channels
                if any(abs(samples[base + ch]) > threshold for ch in range(channels)):
                    first_frame = frame_index
                    break

            self.assertIsNotNone(first_frame)
            onset = first_frame / rate
            self.assertLessEqual(abs(onset - 1.234), 0.012)

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

    def test_final_frame_extends_for_last_voice(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            video, sound, output = (d / name for name in ("video.mp4", "sound.wav", "dub.mp4"))
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=1",
                            "-c:v", "mpeg4", str(video)], check=True)
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.8",
                            str(sound)], check=True)
            mux_video(video, sound, output, CommandRunner(), lambda _message: None, extend_by=.9)
            self.assertGreater(ffprobe_duration(output, CommandRunner()), 1.75)

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

    def test_pitch_and_style_filter_chain_is_valid(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            src = d / "src.wav"
            out = d / "out.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=220:duration=1",
                "-ac", "2", "-ar", "44100", str(src),
            ], check=True)
            filters = _pitch_filters(3.2) + [
                "acompressor=threshold=0.125:ratio=3:attack=5:release=90",
                "volume=1.2",
                "aresample=44100",
                "aformat=sample_fmts=s16:channel_layouts=stereo",
            ]
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src), "-af", ",".join(filters),
                "-c:a", "pcm_s16le", str(out),
            ], check=True)
            dur = ffprobe_duration(out, CommandRunner())
            self.assertTrue(0.95 <= dur <= 1.05)


@unittest.skipUnless(platform.system() == "Darwin" and shutil.which("say"), "macOS say required")
class MacTtsTests(unittest.TestCase):
    def test_say_can_render_to_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            out = d / "test.aiff"
            cfg = Config(source="x", output_dir=d, tts_rate=210)
            runner = CommandRunner()
            synthesize_macos("This is a local voice test.", out, cfg, runner)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
