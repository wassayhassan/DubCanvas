import json
import tempfile
import unittest
from pathlib import Path

from anime_dubber.core import (
    Segment,
    atempo_chain,
    coalesce_segments,
    extract_json_array,
    parse_translation_response,
    sanitize_name,
    sanitize_segments,
    source_key,
    srt_timestamp,
    write_srt,
    translation_is_usable,
    _merge_dialogue_guard_intervals,
    _precise_row_bounds,
)


class CoreTests(unittest.TestCase):
    def test_translation_rejects_copied_chinese_and_malformed_output(self):
        self.assertFalse(translation_is_usable("天下武林,门派如林", "天下武林,门派如林", "zh", "en"))
        self.assertFalse(translation_is_usable("唐门", "", "zh", "en"))
        self.assertFalse(translation_is_usable("唐门", '{"id": 1, "text": "Tang Sect"}', "zh", "en"))
        self.assertTrue(translation_is_usable("唐门", "The Tang Sect", "zh", "en"))
        self.assertTrue(translation_is_usable("唐门", "山田太郎", "zh", "ja"))
        self.assertTrue(translation_is_usable("No", "No", "en", "es"))

    def test_srt_timestamp(self):
        self.assertEqual(srt_timestamp(0), "00:00:00,000")
        self.assertEqual(srt_timestamp(61.234), "00:01:01,234")
        self.assertEqual(srt_timestamp(3661.999), "01:01:01,999")

    def test_atempo_chain(self):
        self.assertEqual(atempo_chain(1.0), "atempo=1.000000")
        self.assertEqual(atempo_chain(4.0), "atempo=2.000000,atempo=2.000000")
        self.assertEqual(atempo_chain(0.25), "atempo=0.500000,atempo=0.500000")

    def test_precise_row_bounds_prefers_word_timestamps(self):
        start, end = _precise_row_bounds({
            "start": 10.0,
            "end": 13.0,
            "words": [
                {"start": 10.42, "end": 10.80, "word": "你"},
                {"start": 11.10, "end": 12.55, "word": "好"},
            ],
        })
        self.assertAlmostEqual(start, 10.42, places=3)
        self.assertAlmostEqual(end, 12.55, places=3)

    def test_precise_row_bounds_rejects_wild_word_jump(self):
        start, end = _precise_row_bounds({
            "start": 10.0,
            "end": 13.0,
            "words": [
                {"start": 7.0, "end": 7.4, "word": "bad"},
                {"start": 15.0, "end": 16.0, "word": "bad"},
            ],
        })
        self.assertAlmostEqual(start, 10.0, places=3)
        self.assertAlmostEqual(end, 13.0, places=3)

    def test_source_key_youtube(self):
        self.assertEqual(source_key("https://youtu.be/WH9x3hYwPj0?x=1"), "WH9x3hYwPj0")
        self.assertEqual(source_key("https://www.youtube.com/watch?v=abc123"), "abc123")

    def test_sanitize(self):
        self.assertEqual(sanitize_name("Hello / world!?"), "Hello_world")

    def test_coalesce(self):
        segs = [Segment(0, 1, "你"), Segment(1.1, 2.0, "好"), Segment(4, 5, "再见")]
        out = coalesce_segments(segs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].text, "你 好")
        self.assertEqual(out[0].end, 2.0)


    def test_sanitize_segments_repairs_and_deduplicates_micro_segments(self):
        segs = [
            Segment(10.00, 10.02, "Yes"),
            Segment(10.02, 10.00, "Yes"),
            Segment(10.04, 10.06, "Yes"),
            Segment(11.00, 10.90, "Next"),
            Segment(12.00, 12.02, "-"),
        ]
        out = sanitize_segments(segs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].text, "Yes")
        self.assertGreaterEqual(out[0].end - out[0].start, 0.179)
        self.assertEqual(out[1].text, "Next")
        self.assertGreaterEqual(out[1].end - out[1].start, 0.179)

    def test_write_srt_skips_invalid_ranges(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.srt"
            write_srt([Segment(2, 1, "bad"), Segment(3, 4, "good")], p)
            txt = p.read_text(encoding="utf-8")
            self.assertNotIn("bad", txt)
            self.assertIn("good", txt)

    def test_dialogue_guard_intervals_merge_and_pad(self):
        segs = [Segment(1.0, 1.5, "a"), Segment(1.55, 2.0, "b"), Segment(4.0, 4.5, "c")]
        out = _merge_dialogue_guard_intervals(segs, 10.0, pre=0.2, post=0.1)
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0][0], 0.8, places=3)
        self.assertAlmostEqual(out[0][1], 2.1, places=3)
        self.assertAlmostEqual(out[1][0], 3.8, places=3)
        self.assertAlmostEqual(out[1][1], 4.6, places=3)

    def test_dialogue_guard_keeps_brief_inter_turn_pause_on_reduced_stem(self):
        segments = [Segment(1.0, 1.2, "female"), Segment(2.7, 2.9, "male")]
        intervals = _merge_dialogue_guard_intervals(segments, 4.0)
        self.assertEqual(len(intervals), 1)
        self.assertLess(intervals[0][0], 1.0)
        self.assertGreater(intervals[0][1], 2.9)

    def test_parse_json_translation(self):
        raw = '```json\n[{"id":0,"text":"Hello"},{"id":1,"text":"World"}]\n```'
        self.assertEqual(parse_translation_response(raw, [0, 1]), {0: "Hello", 1: "World"})

    def test_parse_line_translation_fallback(self):
        raw = "0|Hello\n1:World"
        self.assertEqual(parse_translation_response(raw, [0, 1]), {0: "Hello", 1: "World"})

    def test_write_srt(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.srt"
            write_srt([Segment(0, 1.5, "你好", "Hello")], p, translated=True)
            txt = p.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:01,500", txt)
            self.assertIn("Hello", txt)


if __name__ == "__main__":
    unittest.main()
