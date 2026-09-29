"""Exercise the NumPy/OpenCV ABI used by visual speech timing."""
import unittest


class DependencyCompatibilityTests(unittest.TestCase):
    def test_opencv_can_process_numpy_frames(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV and NumPy required")
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :, 1] = 255
        gray = cv2.cvtColor(cv2.resize(frame, (50, 50)), cv2.COLOR_BGR2GRAY)
        self.assertEqual(gray.shape, (50, 50))
        self.assertEqual(gray.dtype, np.dtype("uint8"))
        self.assertTrue(np.all(gray > 0))
