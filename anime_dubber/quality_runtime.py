"""Bounded cancellable RPC, conservative memory monitoring, reusable checkpoints."""
from __future__ import annotations
import collections
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tempfile

from .quality_models import digest

_HEAVY_WORKER = threading.Semaphore(1)

def runtime_fingerprints(settings, keys=None):
    """Read package versions without importing/loading any ML model."""
    fingerprints = {}
    script = ("import importlib.metadata as m,json,platform,sys; names="
              "['mlx','mlx-lm','mlx-vlm','mlx-audio','torch','torchaudio','pyannote-audio','kokoro','numpy','transformers']; "
              "d={'python':sys.version,'platform':platform.platform(),'machine':platform.machine(),'packages':{}}; "
              "\nfor name in names:\n try:d['packages'][name]=m.version(name)\n except m.PackageNotFoundError:pass\n"
              "print(json.dumps(d))")
    for key in (keys or ("mlx_python", "translation_python", "review_python", "separator_python", "torch_python")):
        result = subprocess.run([settings[key], "-c", script], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError(f"Could not inspect isolated environment {key}: {result.stderr[-500:]}")
        fingerprints[key] = json.loads(result.stdout)
    return fingerprints

class Worker:
    def __init__(self, role, settings, runner):
        self.role, self.settings, self.runner = role, settings, runner
        self.proc = None
        self.lines = queue.Queue()
        self.tail = collections.deque(maxlen=20)
        self.lease = False
        self.process_lease = None

    def __enter__(self):
        from .core import PipelineError
        while not _HEAVY_WORKER.acquire(timeout=.1):
            self.runner.check_cancel()
        self.lease = True
        try:
            # Each project window can own another backend process. A thread-only
            # semaphore would allow two 30 GB workers on the same Mac.
            from .application.project_lock import folder_lock
            while True:
                self.runner.check_cancel()
                manager = folder_lock(Path(tempfile.gettempdir()) / "DubCanvas-model-workers", timeout=.1)
                try:
                    manager.__enter__()
                    self.process_lease = manager
                    break
                except TimeoutError:
                    continue
            interpreter = (self.settings["clone_python"] if self.role == "index_clone" else
                           self.settings["translation_python"] if self.role == "translator" else
                           self.settings["review_python"] if self.role == "reviewer" else
                           self.settings["separator_python"] if self.role == "separator" else
                           self.settings["torch_python"] if self.role in {"diarizer", "kokoro"}
                           else self.settings["mlx_python"])
            if not interpreter or not Path(interpreter).is_file():
                raise PipelineError(f"Configure isolated Python for {self.role}")
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            env["PYANNOTE_METRICS_ENABLED"] = "0"
            self.proc = subprocess.Popen([interpreter, "-u", "-m", "anime_dubber.quality_worker", self.role],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env, bufsize=1)
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.lines.put(line.rstrip())
        finally:
            self.lines.put(None)

    def call(self, request):
        from .core import PipelineError
        self.runner.check_cancel()
        self.proc.stdin.write(json.dumps({"settings": self.settings, "request": request}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + self.settings["worker_timeout"]
        next_memory = 0
        while True:
            self.runner.check_cancel()
            now = time.monotonic()
            if now > deadline:
                raise PipelineError(f"{self.role} worker exceeded its deadline; completed checkpoints retained")
            if now >= next_memory:
                self._check_memory()
                next_memory = now + 1
            try:
                line = self.lines.get(timeout=.1)
            except queue.Empty:
                continue
            if line is None:
                raise PipelineError(f"{self.role} worker exited: " + "\n".join(self.tail))
            if line.startswith("DUBCANVAS_RESULT "):
                result = json.loads(line.removeprefix("DUBCANVAS_RESULT "))
                if not result["ok"]:
                    raise PipelineError(f"{self.role}: {result['error']}")
                return result["result"]
            if line.startswith("DUBCANVAS_METADATA "):
                getattr(self.runner, "diagnostic", lambda *a, **k: None)("Model worker runtime", **json.loads(line.removeprefix("DUBCANVAS_METADATA ")))
                continue
            if line.startswith("DUBCANVAS_DOWNLOAD "):
                event = json.loads(line.removeprefix("DUBCANVAS_DOWNLOAD "))
                self.runner.progress("__MODEL_DOWNLOAD__|{completed_files}|{total_files}|{model}".format(**event))
                continue
            self.tail.append(line)
            getattr(self.runner, "diagnostic", lambda *a, **k: None)("Model worker output", role=self.role, output=line)

    def _check_memory(self):
        import psutil
        from .core import PipelineError
        try:
            p = psutil.Process(self.proc.pid)
            processes = [p] + p.children(recursive=True)
            rss = sum(x.memory_info().rss for x in processes if x.is_running())
        except psutil.NoSuchProcess:
            return  # The reader reports exit or a final RPC reply without losing its diagnostics.
        # RSS is a conservative guard, not a claim to measure Metal's full footprint.
        if rss > self.settings["worker_memory_gb"] * 1e9:
            raise PipelineError(f"{self.role} exceeded worker RSS budget; reduce context/chunk size")
        used = psutil.virtual_memory().total - psutil.virtual_memory().available
        if sys.platform == "darwin" and used > self.settings["system_memory_gb"] * 1e9:
            raise PipelineError("System memory budget exceeded; checkpoint retained. Close other apps or reduce context")
        getattr(self.runner, "diagnostic", lambda *a, **k: None)("Memory observation", role=self.role, rss_bytes=rss,
            system_used_bytes=used, note="RSS/system observation; Metal physical-footprint validation still required")

    def __exit__(self, *args):
        if self.proc:
            import psutil
            try:
                parent = psutil.Process(self.proc.pid)
                children = parent.children(recursive=True)
                for p in children:
                    p.terminate()
                self.proc.terminate()
                self.proc.wait(timeout=3)
                _, alive = psutil.wait_procs(children, timeout=1)
                for p in alive:
                    p.kill()
            except (psutil.Error, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait()
            if hasattr(self, "reader"):
                self.reader.join(timeout=1)
            for stream in (self.proc.stdin, self.proc.stdout):
                if stream:
                    stream.close()
        if self.lease:
            if self.process_lease:
                self.process_lease.__exit__(None, None, None)
                self.process_lease = None
            _HEAVY_WORKER.release()
            self.lease = False

class Checkpoints:
    def __init__(self, directory, identity, config, runner, environment=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.identity, self.config, self.runner = identity, config, runner
        self.environment = environment or {}

    def path(self, stage, inputs):
        signature = digest({"pipeline_revision": 1, "source": self.identity,
                            "stage": stage, "inputs": inputs, "environment": self.environment})
        return self.directory / f"{stage}_{signature}.json"

    def get(self, stage, inputs):
        path = self.path(stage, inputs)
        if self.config.resume and not self.config.force and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("complete") and data.get("signature") == path.stem:
                    return data["result"]
            except (OSError, ValueError, KeyError):
                pass
        return None

    def put(self, stage, inputs, result):
        from .core import _atomic_json_write
        path = self.path(stage, inputs)
        _atomic_json_write(path, {"signature": path.stem, "complete": True,
                                 "source_sha256": self.identity, "inputs": inputs,
                                 "runtime_versions": self.environment, "result": result})
        return path
