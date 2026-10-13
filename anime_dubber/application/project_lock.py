"""Reentrant locks for manifest transactions across threads and backend processes."""
from contextlib import contextmanager
from pathlib import Path
import os
import threading
import time

_guard = threading.Lock()
_locks = {}
_held = threading.local()


def process_alive(pid) -> bool:
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if os.name == "nt":
            # os.kill(pid, 0) terminates a process on Windows; query instead.
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = api.OpenProcess(0x1000, False, pid)
            if not handle:
                return ctypes.get_last_error() == 5
            try:
                code = wintypes.DWORD()
                return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                api.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, OverflowError):
        return False


@contextmanager
def folder_lock(output_dir: Path, timeout: float = 30):
    path = Path(output_dir).resolve() / ".anime_dubber_project" / "locks" / "projects.lock"
    key = str(path)
    with _guard:
        local_lock = _locks.setdefault(key, threading.RLock())
    with local_lock:
        held = getattr(_held, "paths", set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Project files are busy in another process. Try again after it finishes.")
                    time.sleep(0.02)
            held.add(key)
            _held.paths = held
            try:
                yield
            finally:
                held.remove(key)
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
