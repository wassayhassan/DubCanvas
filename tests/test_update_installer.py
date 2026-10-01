import shutil
import sys
import subprocess
import tempfile
import unittest
import threading
from pathlib import Path


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("zsh"), "macOS updater integration")
class UpdateInstallerTests(unittest.TestCase):
    def test_verified_app_that_does_not_quit_is_terminated_before_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "DubCanvas.app"
            executable = target / "Contents/MacOS/DubCanvas"
            executable.parent.mkdir(parents=True)
            shutil.copyfile("/bin/sleep", executable)
            executable.chmod(0o755)
            (target / "marker").write_text("old app")
            log = root / "update.log"
            app = subprocess.Popen([str(executable), "120"])
            # Reap the child as soon as TERM arrives; otherwise kill -0 would
            # continue to see a zombie instead of an exited application.
            waiter = threading.Thread(target=app.wait, daemon=True)
            waiter.start()
            try:
                result = subprocess.run([
                    "zsh", str(Path(__file__).resolve().parents[1] / "macos/install_update.sh"),
                    str(app.pid), str(target), str(root / "missing-staged.app"), str(log)
                ], timeout=30)
                waiter.join(timeout=2)
                self.assertIsNotNone(app.returncode)
                self.assertIn("sending TERM to the verified DubCanvas process", log.read_text())
                # Missing staged app exercises rollback after the quit fallback.
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((target / "marker").read_text(), "old app")
            finally:
                if app.poll() is None:
                    app.terminate()
                waiter.join(timeout=5)

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
