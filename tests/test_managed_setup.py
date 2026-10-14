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
            (env / '.setup-seeded').write_text('3.13')
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

    def test_incomplete_seed_is_repaired_without_deleting_environment(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / 'worker'; (env / 'bin').mkdir(parents=True)
            (env / 'bin/python').touch()
            (env / 'keep').write_text('completed download')
            with patch.object(setup, 'run') as run:
                setup.install_env('uv', env, '3.13', 'requirements.txt', {})
            self.assertIn('--allow-existing', run.call_args_list[0].args[0])
            self.assertEqual((env / 'keep').read_text(), 'completed download')
            self.assertTrue((env / '.setup-seeded').exists())

    def test_standalone_installer_can_import_bundled_backend_without_pythonpath(self):
        import subprocess
        import sys
        script = Path(setup.__file__).resolve()
        code = "import runpy,sys; runpy.run_path(sys.argv[1],run_name='installer_module'); import anime_dubber.quality_models; print('bundled backend found')"
        result = subprocess.run([sys.executable, '-I', '-c', code, str(script)], capture_output=True, text=True, check=True)
        self.assertIn('bundled backend found', result.stdout)

    def test_checkpoint_mirror_fallback_keeps_both_integrity_checks(self):
        with patch.object(setup, 'download', side_effect=[OSError('mirror offline'), None]) as download:
            setup.download_checkpoint(Path('weights'))
        self.assertEqual(download.call_count, 2)
        self.assertIn('zenodo.org', download.call_args.args[0])
        for call in download.call_args_list:
            self.assertEqual(call.kwargs['sha256'], setup.CHECKPOINT_SHA256)
            self.assertEqual(call.kwargs['md5'], setup.CHECKPOINT_MD5)

    def test_bandit_training_wrapper_is_normalized_without_weakening_strict_loading(self):
        from anime_dubber.quality_models import bandit_state_dict
        tensor = object()
        self.assertEqual(bandit_state_dict({'state_dict': {'model.band.weight': tensor}}), {'band.weight': tensor})
        self.assertEqual(bandit_state_dict({'band.weight': tensor}), {'band.weight': tensor})
        self.assertEqual(bandit_state_dict({'state_dict': {'model.band.weight': tensor, 'loss_handler.l1_weight': tensor}}), {'band.weight': tensor})
        with self.assertRaises(ValueError): bandit_state_dict({'state_dict': {'model.band.weight': tensor, 'band.bias': tensor}})
