import math
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from anime_dubber.characters import (
    AudioFeatures,
    _acoustic_features,
    _greedy_cluster,
    _infer_voice_class,
    _style_for_segment,
    cosine,
    analyze_characters,
    write_character_map,
    update_character_override,
)
from anime_dubber.core import CommandRunner, Segment, Config, _chatterbox_reference


class CharacterLogicTests(unittest.TestCase):
    def test_reference_priority_and_opt_out(self):
        config = Config(source="video", output_dir=Path("."), chatterbox_reference_audio="")
        profile = {"suggested_reference_audio": "/auto/character.wav", "auto_reference_enabled": True}
        self.assertEqual(_chatterbox_reference(profile, config), "/auto/character.wav")
        profile["reference_audio"] = "/chosen.wav"
        self.assertEqual(_chatterbox_reference(profile, config), "/chosen.wav")
        profile["reference_audio"] = ""
        config.chatterbox_reference_audio = "/global.wav"
        self.assertEqual(_chatterbox_reference(profile, config), "/auto/character.wav")
        profile["suggested_reference_audio"] = ""
        self.assertEqual(_chatterbox_reference(profile, config), "")
        self.assertEqual(_chatterbox_reference({}, config), "/global.wav")
        config.chatterbox_reference_audio = ""
        profile["auto_reference_enabled"] = False
        self.assertEqual(_chatterbox_reference(profile, config), "")
        config.auto_voice_references = False
        profile["auto_reference_enabled"] = True
        self.assertEqual(_chatterbox_reference(profile, config), "")

    def tone(self, freq, amp=0.2, seconds=1.0, sr=16000):
        t = np.arange(int(sr * seconds), dtype=np.float32) / sr
        return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    def test_pitch_and_voice_class(self):
        low = _acoustic_features(self.tone(120))
        high = _acoustic_features(self.tone(260))
        self.assertTrue(105 <= low.f0_median <= 140)
        self.assertTrue(235 <= high.f0_median <= 285)
        self.assertEqual(_infer_voice_class(low.f0_median)[0], "male")
        self.assertEqual(_infer_voice_class(high.f0_median)[0], "female")

    def test_acoustic_embeddings_cluster_two_speakers(self):
        embs = [_acoustic_features(self.tone(f)).embedding for f in (120, 125, 280, 275)]
        labels = _greedy_cluster(embs, max_speakers=6, threshold=0.91)
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[2], labels[3])
        self.assertNotEqual(labels[0], labels[2])
        self.assertGreater(cosine(embs[0], embs[1]), 0.95)

    def test_similar_adjacent_embeddings_do_not_merge_clearly_opposed_voices(self):
        female = AudioFeatures(f0_median=250, voiced_ratio=.8)
        male = AudioFeatures(f0_median=115, voiced_ratio=.8)
        labels = _greedy_cluster([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
                                 max_speakers=6, threshold=.91,
                                 features=[female, male, female])
        self.assertEqual(labels[0], labels[2])
        self.assertNotEqual(labels[0], labels[1])

    def test_uncertain_pitch_does_not_force_a_new_character(self):
        unknown = AudioFeatures(f0_median=0, voiced_ratio=.1)
        female = AudioFeatures(f0_median=245, voiced_ratio=.8)
        labels = _greedy_cluster([[1.0, 0.0], [1.0, 0.0]],
                                 max_speakers=6, threshold=.91, features=[female, unknown])
        self.assertEqual(labels, [0, 0])

    def test_style_detection(self):
        baseline = {"rms": -25.0, "f0": 130.0, "flat": 0.02}
        shout = AudioFeatures(rms_db=-15, f0_median=170, voiced_ratio=.9, flatness=.02)
        whisper = AudioFeatures(rms_db=-36, f0_median=0, voiced_ratio=.20, flatness=.05)
        normal = AudioFeatures(rms_db=-25, f0_median=130, voiced_ratio=.9, flatness=.02)
        self.assertEqual(_style_for_segment(shout, baseline, "住手！")[0], "shouting")
        self.assertEqual(_style_for_segment(whisper, baseline, "别出声")[0], "whispering")
        self.assertEqual(_style_for_segment(normal, baseline, "你好")[0], "normal")


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
