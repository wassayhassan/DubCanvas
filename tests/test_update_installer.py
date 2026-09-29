import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("zsh"), "macOS updater integration")
class UpdateInstallerTests(unittest.TestCase):
    def test_failed_replacement_restores_original_and_preserves_helper_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "DubCanvas.app"; target.mkdir()
            staged = root / "missing-staged.app"
            (target / "marker").write_text("old app")
            helper_directory = root / "helper"; helper_directory.mkdir()
            helper = helper_directory / "install_update.sh"
            shutil.copyfile(Path(__file__).resolve().parents[1] / "macos/install_update.sh", helper)
            result = subprocess.run(["zsh", str(helper), "99999999", str(target), str(staged), str(root / "update.log")], timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((target / "marker").read_text(), "old app")
            self.assertTrue(helper.exists())
