import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from anime_dubber.characters import (
    analyze_characters,
    write_character_map,
    update_character_override,
)
from anime_dubber.core import CommandRunner, Segment


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class CharacterIntegrationTests(unittest.TestCase):
    def test_auto_voice_clips_are_distinct_and_manual_choice_survives_cached_analysis(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rate = 16000
            def speech(freq):
                t = np.arange(rate * 3, dtype=np.float32) / rate
                return (.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
            vocals = root / "vocals.wav"
            sf.write(vocals, np.concatenate([speech(f) for f in (120, 280, 125, 275)]), rate)
            segments = [Segment(0, 3, "甲"), Segment(3, 6, "乙"),
                        Segment(6, 9, "甲"), Segment(9, 12, "乙")]
            work = root / "work"
            map_path = root / "characters.json"
            options = dict(resume=True, max_speakers=6, speaker_threshold=.91,
                           speaker_backend="acoustic", override_path=map_path)
            profiles, payload = analyze_characters(vocals, segments, work, root,
                                                    CommandRunner(), lambda _: None, **options)
            self.assertEqual(len(profiles), 2)
            paths = [Path(p.suggested_reference_audio) for p in profiles]
            self.assertEqual(len(set(paths)), 2)
            self.assertTrue(all(p.exists() for p in paths))
            self.assertTrue(all(p.reference_timing for p in profiles))
            write_character_map(payload, map_path)
            update_character_override(map_path, profiles[0].id,
                                      {"reference_audio": "/my/manual.wav", "auto_reference_enabled": False})
            recovered, _ = analyze_characters(vocals, segments, work, root,
                                              CommandRunner(), lambda _: None, **options)
            chosen = next(p for p in recovered if p.id == profiles[0].id)
            self.assertEqual(chosen.reference_audio, "/my/manual.wav")
            self.assertFalse(chosen.auto_reference_enabled)
            self.assertEqual(chosen.suggested_reference_audio, profiles[0].suggested_reference_audio)

    def test_overlapping_and_short_speech_does_not_create_a_voice_clone(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rate = 16000
            t = np.arange(rate * 4, dtype=np.float32) / rate
            audio = .2 * np.sin(2 * np.pi * 130 * t)
            vocals = root / "vocals.wav"
            sf.write(vocals, audio, rate)
            from anime_dubber.characters import CharacterProfile, _choose_voice_references
            profiles = [CharacterProfile(id="a", display_name="A", f0_median=130),
                        CharacterProfile(id="b", display_name="B", f0_median=130)]
            segments = [Segment(0, 3, "A", speaker_id="a"),
                        Segment(1, 3, "B", speaker_id="b"),
                        Segment(3, 3.5, "A", speaker_id="a")]
            _choose_voice_references(vocals, segments, profiles, root, CommandRunner(), lambda _: None)
            self.assertTrue(all(not p.suggested_reference_audio for p in profiles))

    def test_four_second_isolated_speech_falls_back_before_chatterbox(self):
        import soundfile as sf
        from anime_dubber.characters import CharacterProfile, _choose_voice_references
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            vocals = root / "voice.wav"
            t = np.arange(16000 * 4, dtype=np.float32) / 16000
            sf.write(vocals, .2 * np.sin(2 * np.pi * 140 * t), 16000)
            profile = CharacterProfile(id="CHAR_001", display_name="Speaker", role="lead",
                                       voice_class="male")
            _choose_voice_references(vocals, [Segment(0, 4, "你好", speaker_id="CHAR_001")],
                                     [profile], root, CommandRunner(), lambda _: None)
            self.assertFalse(profile.suggested_reference_audio)

            from anime_dubber.providers.tts import synthesize_chatterbox
            with patch("anime_dubber.providers.tts.chatterbox_available", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "more than 5 seconds"):
                    synthesize_chatterbox("Hello", root / "output.wav", reference_audio=str(vocals))

    def test_cached_speaker_analysis_can_add_references_later(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rate = 16000
            t = np.arange(rate * 3, dtype=np.float32) / rate
            vocals = root / "vocals.wav"
            sf.write(vocals, (.2 * np.sin(2 * np.pi * 130 * t)).astype(np.float32), rate)
            segments = [Segment(0, 3, "甲")]
            options = dict(resume=True, speaker_backend="acoustic")
            first, _ = analyze_characters(vocals, segments, root / "work", root,
                                          CommandRunner(), lambda _: None,
                                          make_voice_references=False, **options)
            self.assertFalse(first[0].suggested_reference_audio)
            second, _ = analyze_characters(vocals, segments, root / "work", root,
                                           CommandRunner(), lambda _: None,
                                           make_voice_references=True, **options)
            self.assertFalse(second[0].suggested_reference_audio)
            cache = root / "work" / "character_analysis.json"
            old = json.loads(cache.read_text(encoding="utf-8"))
            old["reference_version"] = 1
            old["characters"][0]["suggested_reference_audio"] = str(vocals)
            cache.write_text(json.dumps(old), encoding="utf-8")
            updated, payload = analyze_characters(vocals, segments, root / "work", root,
                                                  CommandRunner(), lambda _: None,
                                                  make_voice_references=True, **options)
            self.assertEqual(payload["reference_version"], 2)
            self.assertFalse(updated[0].suggested_reference_audio)

    def test_full_character_analysis_on_synthetic_audio(self):
        try:
            import soundfile as sf
        except Exception:
            self.skipTest("soundfile unavailable")
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            sr = 16000
            def tone(freq):
                t = np.arange(sr, dtype=np.float32) / sr
                return 0.2 * np.sin(2 * np.pi * freq * t)
            audio = np.concatenate([tone(120), tone(280), tone(125), tone(275)]).astype(np.float32)
            src = d / "vocals.wav"
            sf.write(src, audio, sr, subtype="PCM_16")
            segs = [
                Segment(0, 1, "甲说话"), Segment(1, 2, "乙说话"),
                Segment(2, 3, "甲继续"), Segment(3, 4, "乙继续"),
            ]
            profiles, payload = analyze_characters(
                src, segs, d / "work", d, CommandRunner(), lambda _m: None,
                resume=False, force=True, max_speakers=6, speaker_threshold=.91,
                series_id="test-series", available_voices=[], override_path=None, speaker_backend="acoustic",
            )
            self.assertEqual(len(profiles), 2)
            self.assertEqual(segs[0].speaker_id, segs[2].speaker_id)
            self.assertEqual(segs[1].speaker_id, segs[3].speaker_id)
            self.assertNotEqual(segs[0].speaker_id, segs[1].speaker_id)
            self.assertIn("characters", payload)
            self.assertTrue(all(s.style in {"normal", "shouting", "whispering"} for s in segs))


if __name__ == "__main__":
    unittest.main()
