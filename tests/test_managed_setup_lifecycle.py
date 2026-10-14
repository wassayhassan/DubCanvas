import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

@unittest.skipIf(os.name == 'nt', 'POSIX app supervisor')
class ManagedSetupLifecycleTests(unittest.TestCase):
    def test_app_exit_stops_installer_and_active_worker(self):
        root = Path(__file__).resolve().parents[1]
        supervisor = root / 'macos/managed_setup_launch.sh'
        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / 'worker.pid'
            parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            worker_code = "import os,time; from pathlib import Path; Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(pid_file)
            installer_code = "import signal,sys; from tools.managed_setup import run,interrupted; signal.signal(signal.SIGTERM,interrupted); run([sys.executable,'-c',sys.argv[1]])"
            child = subprocess.Popen(['/bin/sh', str(supervisor), str(parent.pid), sys.executable,
                                      '-c', installer_code, worker_code], cwd=root,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not pid_file.exists() and time.monotonic() < deadline:
                    if child.poll() is not None: self.fail('Installer exited before worker started')
                    time.sleep(.02)
                self.assertTrue(pid_file.exists())
                worker_pid = int(pid_file.read_text())
                parent.terminate(); parent.wait(timeout=5)
                self.assertEqual(child.wait(timeout=10), 130)
                with self.assertRaises(ProcessLookupError): os.kill(worker_pid, 0)
            finally:
                if parent.poll() is None: parent.kill(); parent.wait()
                if child.poll() is None: child.terminate(); child.wait(timeout=10)

    def test_supervisor_preserves_success_and_failure_status(self):
        supervisor = Path(__file__).resolve().parents[1] / 'macos/managed_setup_launch.sh'
        for status in (0, 7):
            result = subprocess.run(['/bin/sh', str(supervisor), str(os.getpid()), sys.executable,
                                     '-c', f'raise SystemExit({status})'], timeout=10)
            self.assertEqual(result.returncode, status)
