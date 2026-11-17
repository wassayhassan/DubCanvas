"""Integration contracts for cache boundaries, recovery, vocals and release trust."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from anime_dubber.core import CommandRunner, Config, Segment, CancelledError


class PipelineImprovementsTests(unittest.TestCase):
    def test_context_cache_only_invalidates_affected_neighbors(self):
        from anime_dubber.translation import translate
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = Config(source='video', output_dir=root, source_language='zh', translation='ollama')
            rows = [Segment(i*2, i*2+1, f'你好{i}', speaker_id='speaker1') for i in range(20)]
            requested = []
            def generate(**kwargs):
                batch = json.loads(kwargs['prompt'].split('Input: ')[1])
                requested.extend(row['id'] for row in batch)
                self.assertTrue(all(row['speaker']=='speaker1' and row['syllable_budget']==4 for row in batch))
                return json.dumps([{'id': row['id'], 'text': f"Hello number {row['id']}"} for row in batch])
            with patch('anime_dubber.providers.translation.ollama_generate', side_effect=generate):
                translate(rows, config, root, CommandRunner(), lambda _: None, 'ollama')
                self.assertEqual(requested, list(range(20)))
                requested.clear()
                translate(rows, config, root, CommandRunner(), lambda _: None, 'ollama')
                self.assertEqual(requested, [])
                rows[10].text = '再见'
                translate(rows, config, root, CommandRunner(), lambda _: None, 'ollama')
                self.assertEqual(requested, list(range(7, 14)))

    def test_asr_resume_keeps_good_chunks_and_repairs_only_low_confidence_cue(self):
        from anime_dubber.asr_chunks import transcribe_chunks
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio, original = root/'vocals.wav', root/'original.wav'
            sf.write(audio, np.zeros(21000), 1000)
            sf.write(original, np.ones(21000)*.1, 1000)
            config = Config(source='video', output_dir=root, source_language='zh', asr_chunk_seconds=10)
            row = {'start':.5, 'end':1., 'text':'你好', 'avg_logprob':-.1}
            with patch('anime_dubber.quality_runtime.Worker') as Worker:
                worker = Worker.return_value.__enter__.return_value
                worker.call.side_effect = [
                    {'language':'zh','segments':[dict(row, avg_logprob=-2)]},
                    {'language':'zh','segments':[dict(row, start=0., end=.4)]},
                    CancelledError('paused')]
                with self.assertRaises(CancelledError):
                    transcribe_chunks(audio, config, root, CommandRunner(), lambda _:None, 'transcribe', original)
                self.assertEqual(len(list(root.glob('asr_chunks_*/*.json'))), 1)
                worker.call.reset_mock()
                worker.call.side_effect = None
                worker.call.return_value = {'language':'zh','segments':[row]}
                result = transcribe_chunks(audio, config, root, CommandRunner(), lambda _:None, 'transcribe', original)
                self.assertEqual(worker.call.call_count, 2)
                self.assertEqual(len(result['segments']), 3)
                self.assertAlmostEqual(result['segments'][0]['start'], .5)
                self.assertGreater(result['segments'][2]['start'], 20)

    def test_vocals_mask_catches_untranscribed_sound_and_preserves_silence_bed(self):
        from anime_dubber.audio_analysis import activity, blend_background, timing_windows
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); rate = 8000
            original, vocals, bed = root/'original.wav', root/'vocals.wav', root/'bed.wav'
            voice = np.zeros(4*rate); voice[2*rate:round(2.3*rate)] = .2
            sf.write(original, np.ones(4*rate)*.2, rate)
            sf.write(vocals, voice, rate)
            sf.write(bed, np.zeros(4*rate), rate)
            runner = CommandRunner()
            intervals = activity(vocals, runner)
            result = blend_background(original, bed, intervals, root/'result.wav', 4, runner, lambda _:None)
            samples, _ = sf.read(result)
            self.assertLess(np.max(abs(samples[round(2.05*rate):round(2.25*rate)])), .001)
            self.assertAlmostEqual(float(samples[rate]), .2, places=3)
            windows = timing_windows([Segment(.5,1,'a'),Segment(3,3.5,'b')], intervals, 4)
            self.assertLessEqual(windows[0][1], intervals[0][0])
            self.assertGreaterEqual(windows[1][0], intervals[0][1])

    def test_signed_updates_reject_tampering_unsigned_rollback_and_wrong_key(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from macos.update_signing import sign, verify
        key = Ed25519PrivateKey.generate()
        private = base64.b64encode(key.private_bytes_raw()).decode()
        public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
        manifest = dict(schema=2,revision='a'*40,sourceTimestamp=123,version='4.0',sha256='b'*64)
        signed = sign(manifest, private, public)
        verify(signed, public, 122)
        for invalid, floor in [(manifest,0),(dict(signed,sha256='c'*64),0),(signed,124)]:
            with self.assertRaises(Exception): verify(invalid,public,floor)
        other = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
        with self.assertRaises(Exception): verify(signed,other)
        with self.assertRaises(ValueError): sign(manifest,private,other)
