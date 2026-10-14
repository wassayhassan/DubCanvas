import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import managed_setup as setup

class ManagedSetupTests(unittest.TestCase):
    def test_download_failure_preserves_existing_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'weights.ckpt'
            target.write_bytes(b'old')
            with patch.object(setup.urllib.request, 'urlopen', side_effect=OSError('offline')):
                with self.assertRaises(OSError): setup.download('https://example.com', target, md5='wrong')
            self.assertEqual(target.read_bytes(), b'old')
            self.assertFalse(target.with_suffix('.ckpt.partial').exists())

    def test_verified_download_is_reused_and_invalid_download_never_published(self):
        import io
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'weights.ckpt'
            expected = hashlib.md5(b'good').hexdigest()
            with patch.object(setup.urllib.request, 'urlopen', return_value=io.BytesIO(b'wrong')):
                with self.assertRaises(RuntimeError): setup.download('https://example.com', target, md5=expected)
            self.assertFalse(target.exists())
            with patch.object(setup.urllib.request, 'urlopen', return_value=io.BytesIO(b'good')):
                setup.download('https://example.com', target, md5=expected)
            with patch.object(setup.urllib.request, 'urlopen') as network:
                setup.download('https://example.com', target, md5=expected)
                network.assert_not_called()

    def test_environment_retry_repairs_existing_environment_and_checks_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / 'worker'; (env / 'bin').mkdir(parents=True)
            python = env / 'bin/python'; python.touch()
            with patch.object(setup, 'run') as run:
                setup.install_env('uv', env, '3.13', 'requirements.txt', {})
            self.assertEqual(run.call_count, 2)
            self.assertIn('install', run.call_args_list[0].args[0])
            self.assertEqual(run.call_args_list[-1].args[0][-3:], ['-m', 'pip', 'check'])

    def test_core_failure_never_marks_runtime_ready(self):
        with tempfile.TemporaryDirectory() as td, patch.object(setup, 'install_env', side_effect=RuntimeError('install failed')):
            with self.assertRaises(RuntimeError): setup.provision(Path(td), Path('uv'))
            self.assertFalse((Path(td) / 'core-ready.json').exists())

    def test_core_ready_reuses_runtime_without_reinstalling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / 'core/bin').mkdir(parents=True)
            (root / 'core/bin/python').touch()
            setup.atomic_json(root / 'core-ready.json', {'fingerprint': setup.core_fingerprint()})
            with patch.object(setup, 'install_env') as install, patch.object(setup, 'run'):
                setup.provision(root, Path('uv'))
            install.assert_not_called()
