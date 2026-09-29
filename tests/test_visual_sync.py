import json
import sys
import tempfile
import types
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from anime_dubber.core import CommandRunner, Segment
from anime_dubber.visual_sync import _audible_onset, _mouth_motion, _visual_onset, align_dub_to_visible_speech


class VisualTimingTests(unittest.TestCase):
    def test_clear_onset_near_boundary_is_detected(self):
        times = [-.3, -.2, -.1, 0, .1, .2, .3]
        self.assertAlmostEqual(_visual_onset(times, [0, 0, 0, 0, 10, 11, 9], 0), .1)

    def test_audible_onset_accounts_for_tts_lead_in(self):
        with tempfile.TemporaryDirectory() as td:
            clip = Path(td) / "voice.wav"
            samples = array("h", [0] * 800 + [12000] * 1200)
            with wave.open(str(clip), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(10000)
                output.writeframes(samples.tobytes())
            self.assertAlmostEqual(_audible_onset(clip), .08, places=2)

    def test_ambiguous_or_far_onset_does_not_shift(self):
        times = [-.3, -.2, -.1, 0, .1, .2, .3]
        self.assertIsNone(_visual_onset(times, [10, 10, 10, 0, 9, 10, 9], 0))
        self.assertIsNone(_visual_onset(times, [0, 0, 0, 0, 0, 0, 10], 0))

    def test_multiple_faces_skip_visual_timing(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is needed for frame extraction")
        class Capture:
            def set(self, *_): return True
            def read(self): return True, np.zeros((120, 160, 3), dtype=np.uint8)
            def get(self, prop): return 24 if prop == 1 else 1000
        cv2 = types.SimpleNamespace(CAP_PROP_FPS=1, CAP_PROP_POS_MSEC=2,
                                    resize=lambda frame, _size: frame,
                                    cvtColor=lambda frame, _mode: frame[:, :, 0], COLOR_BGR2GRAY=3)
        detector = types.SimpleNamespace(detectMultiScale=lambda *_args, **_kwargs:
                                         [(0, 0, 80, 80), (80, 0, 80, 80)])
        self.assertIsNone(_mouth_motion(Path("unused"), 1, Capture(), detector, cv2))

    def test_visual_timing_is_bounded_preserves_subtitles_and_resumes(self):
        class Capture:
            def isOpened(self): return True
            def release(self): pass
        class Detector:
            def empty(self): return False
        cv2 = types.SimpleNamespace(data=types.SimpleNamespace(haarcascades="/models/"),
                                    CascadeClassifier=lambda _: Detector(),
                                    VideoCapture=lambda _: Capture())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "video.mp4"; video.write_bytes(b"video")
            clips = [root / "one.wav", root / "two.wav"]
            for clip in clips: clip.write_bytes(b"voice")
            cues = [Segment(1, 1.5, "source", "translated"), Segment(2, 2.5, "source 2", "translated 2")]
            times = [.7, .8, .9, 1.0, 1.1, 1.2, 1.3]
            with patch.dict(sys.modules, {"cv2": cv2}), \
                 patch("anime_dubber.visual_sync.ffprobe_duration", return_value=.6), \
                 patch("anime_dubber.visual_sync._mouth_motion", side_effect=[
                     (times, [0, 0, 0, 0, 9, 10, 9]),
                     None,
                 ]) as inspect:
                adjusted = align_dub_to_visible_speech(video, cues, clips, root, CommandRunner(),
                                                       lambda _: None, total_duration=3)
                reused = align_dub_to_visible_speech(video, cues, clips, root, CommandRunner(),
                                                     lambda _: None, total_duration=3)
            self.assertEqual(inspect.call_count, 2)
            self.assertAlmostEqual(adjusted[0].start, 1.1)
            self.assertEqual(adjusted[1].start, 2)
            self.assertEqual([s.start for s in cues], [1, 2])
            self.assertEqual([s.start for s in reused], [s.start for s in adjusted])
            report = next(root.glob("visual_timing_*.json"))
            self.assertEqual(json.loads(report.read_text())["offsets"], [.1, 0.0])

    def test_shift_that_would_collide_with_next_line_is_skipped(self):
        class Capture:
            def isOpened(self): return True
            def release(self): pass
        cv2 = types.SimpleNamespace(data=types.SimpleNamespace(haarcascades="/models/"),
                                    CascadeClassifier=lambda _: types.SimpleNamespace(empty=lambda: False),
                                    VideoCapture=lambda _: Capture())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "video.mp4"; video.write_bytes(b"video")
            clips = [root / "one.wav", root / "two.wav"]
            for clip in clips: clip.write_bytes(b"voice")
            cues = [Segment(1, 1.5, "one"), Segment(1.65, 2.2, "two")]
            with patch.dict(sys.modules, {"cv2": cv2}), \
                 patch("anime_dubber.visual_sync.ffprobe_duration", return_value=.6), \
                 patch("anime_dubber.visual_sync._mouth_motion", side_effect=[
                     ([.7, .8, .9, 1, 1.1, 1.2, 1.3], [0, 0, 0, 0, 9, 10, 9]), None,
                 ]):
                adjusted = align_dub_to_visible_speech(video, cues, clips, root, CommandRunner(),
                                                       lambda _: None, total_duration=3)
            self.assertEqual([s.start for s in adjusted], [1, 1.65])


if __name__ == "__main__":
    unittest.main()
