import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from anime_dubber.core import CommandRunner
from anime_dubber.localization import Glossary
from anime_dubber.quality_timing import validate_source_alignment
from anime_dubber.quality_export import source_duration, mux_preserving_video, verify_video_copy

class ReviewFixTests(unittest.TestCase):
    def test_alignment_requires_complete_ordered_bounded_text(self):
        full = '你好，宗主已经死了。'
        valid = [{'text': full, 'start_time': .1, 'end_time': 1.9}]
        validate_source_alignment(full, valid, 2)
        for bad in ([{'text': '你好', 'start_time': .1, 'end_time': .5}],
                    [{'text': full, 'start_time': 1, 'end_time': 3}],
                    [{'text': full, 'start_time': float('nan'), 'end_time': 1}]):
            with self.assertRaises(ValueError): validate_source_alignment(full, bad, 2)

    def test_stale_glossaries_merge_evidence_and_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'g.json'
            a, b = Glossary(path, {}), Glossary(path, {})
            row = {'source': '宗主', 'english': 'Sect Master'}
            a.learn([row], '宗主', 'Sect Master', 'episode A')
            b.learn([row], '宗主', 'Sect Master', 'episode B')
            a.save(); b.save()
            self.assertEqual(b.entries['宗主']['evidence'], ['episode A', 'episode B'])
            self.assertTrue(b.entries['宗主']['confirmed'])
            c, d = Glossary(path, {}), Glossary(path, {})
            c.entries['宗主']['pronunciation'] = 'sekt master'
            c.ranks = ['one', 'two']; c.save(); d.save()
            self.assertEqual(d.entries['宗主']['pronunciation'], 'sekt master')
            self.assertEqual(d.ranks, ['one', 'two'])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'media tools unavailable')
    def test_short_audio_keeps_entire_video_and_unsupported_codec_uses_mkv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); runner = CommandRunner()
            for codec in ('mpeg4', 'ffv1'):
                source = root / (codec + '.mkv')
                subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'color=s=160x90:r=25:d=4', '-f', 'lavfi', '-i',
                    'sine=sample_rate=48000:duration=2', '-c:v', codec, '-c:a', 'aac', str(source)], check=True)
                audio = root / 'dub.m4a'
                subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'sine=sample_rate=48000:duration=4', '-c:a', 'aac', str(audio)], check=True)
                self.assertAlmostEqual(source_duration(source, runner), 4, delta=.05)
                output = mux_preserving_video(source, audio, root / (codec + '.mp4'), runner, lambda _: None)
                self.assertEqual(verify_video_copy(source, output, runner)['packet_count'], 100)
                if codec == 'ffv1': self.assertEqual(output.suffix, '.mkv')
