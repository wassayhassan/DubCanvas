"""Append-only diagnostics with readable stage sections and structured JSONL."""
from __future__ import annotations

import contextlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__

_local = threading.local()
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def clean_text(value: object) -> str:
    return _ANSI.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n").strip()


class DiagnosticLog:
    def __init__(self, directory: Path, job_id: str, config: dict):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{job_id}.log"
        self.json_path = directory / f"{job_id}.jsonl"
        self.job_id = job_id
        self.stage = "preparing"
        self.started = self.stage_started = time.monotonic()
        self.lock = threading.RLock()
        self.initialized = False
        self.secrets = [str(v) for k, v in config.items() if
                        any(term in k.lower() for term in ("api_key", "password", "token", "secret")) and v]
        self.secrets += [value for key, value in os.environ.items() if value and
                         key in {"ELEVENLABS_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}]
        versions = {}
        for package in ("numpy", "opencv-python-headless", "mlx-whisper", "mlx-lm", "faster-whisper", "torch", "chatterbox-tts", "demucs"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "not installed"
        self.write("Job started", details={"app_version": __version__, "job_id": job_id,
                   "platform": platform.platform(), "python": sys.version, "models": versions,
                   "config": config})
        self.initialized = True

    def redact(self, value):
        if isinstance(value, dict):
            return {k: "<redacted>" if any(term in str(k).lower() for term in
                    ("api_key", "password", "token", "secret", "authorization")) else self.redact(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(v) for v in value]
        if not isinstance(value, str):
            return value
        value = clean_text(value)
        for secret in self.secrets:
            value = value.replace(secret, "<redacted>")
        value = re.sub(r"(?i)([?&](?:token|key|api_key|auth|signature|sig)=)[^&\s]+", r"\1<redacted>", value)
        value = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1<redacted>@", value)
        return re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._-]+", r"\1<redacted>", value)

    def change_stage(self, stage: str):
        if stage == self.stage:
            return
        with self.lock:
            self.write("Stage finished", details={"duration_seconds": round(time.monotonic() - self.stage_started, 3)})
            self.stage = stage
            self.stage_started = time.monotonic()
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n=== {stage.replace('_', ' ').upper()} ===\n")
            except OSError:
                # write() reports persistence failures without stopping processing.
                pass
            self.write("Stage started")

    def write(self, message: object, level: str = "info", details: dict | None = None) -> dict:
        payload = self.redact({"timestamp": datetime.now(timezone.utc).isoformat(), "job_id": self.job_id,
                   "stage": self.stage, "level": level, "elapsed_seconds": round(time.monotonic() - self.started, 3),
                   "message": clean_text(message), "details": details or {}})
        with self.lock:
            try:
                with self.json_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                with self.path.open("a", encoding="utf-8") as handle:
                    prefix = f"{payload['timestamp']} +{payload['elapsed_seconds']:.3f}s [{self.stage}] [{level.upper()}] "
                    for line in payload["message"].splitlines():
                        handle.write(prefix + line + "\n")
                    if payload["details"]:
                        handle.write(json.dumps(payload["details"], ensure_ascii=False, indent=2, default=str) + "\n")
            except OSError as exc:
                if not self.initialized:
                    raise  # Service can choose a writable fallback at startup.
                payload["details"]["log_write_error"] = self.redact(str(exc))
        return payload


@contextlib.contextmanager
def capture_model_output(log: DiagnosticLog):
    previous = getattr(_local, "log", None)
    _local.log = log
    try:
        yield
    finally:
        _local.log = previous


class DiagnosticStream:
    """Capture Python model prints in the executing job without global redirection."""
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        log = getattr(_local, "log", None)
        if log and clean_text(text):
            log.write(text, "debug", {"origin": "model output"})
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)
