from __future__ import annotations

import json
import hashlib
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..core import Config, source_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return value


def _redacted_config(config: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(config)
    for key in ("elevenlabs_api_key", "api_key", "token", "password"):
        if key in data and data[key]:
            data[key] = "<redacted>"
    tts = data.get("tts")
    if isinstance(tts, dict):
        tts = dict(tts)
        if tts.get("api_key"):
            tts["api_key"] = "<redacted>"
        data["tts"] = tts
    return _safe(data)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _upgrade(data: dict) -> dict:
    if data.get("schema_version") == 2:
        data.setdefault("analysis_revision", 0)
        data.setdefault("analysis_fingerprints", {})
        return data
    artifacts = dict(data.get("artifacts") or {})
    data["schema_version"] = 2
    data.setdefault("name", "")
    data.setdefault("subtitles", {})
    data.setdefault("analysis_revision", 0)
    data.setdefault("analysis_fingerprints", {})
    if "dubbed_video" in artifacts and not data.get("dubs"):
        data["dubs"] = [{
            "id": "legacy", "name": "English — Legacy", "language": "en",
            "source_language": "zh", "status": data.get("status", "completed"),
            "stage": data.get("stage", "completed"),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "config": data.get("config") or {}, "artifacts": artifacts,
            "warnings": [], "error": data.get("last_error"),
        }]
    else:
        data.setdefault("dubs", [])
    return data


class ProjectStore:
    """Small durable manifest used by both SwiftUI and CLI.

    Heavy work stays in .anime_dubber_work; this directory is intentionally
    lightweight so the Projects screen can load without scanning media caches.
    """

    def __init__(self, output_dir: Path, source: str):
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.project_id = source_key(source, self.output_dir)
        self.root = self.output_dir / ".anime_dubber_project"
        self.manifest_path = self.root / "projects" / f"{self.project_id}.json"
        self.log_path = self.root / "logs" / f"{self.project_id}.log"

    def load(self) -> dict:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return _upgrade(data) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _validated_name(self, name: str) -> str:
        value = name.strip()
        if not value:
            raise ValueError("Project name is required")
        key = " ".join(value.split()).casefold()
        for path in (self.root / "projects").glob("*.json"):
            if path == self.manifest_path:
                continue
            try:
                other = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(other, dict) and " ".join(str(other.get("name") or "").split()).casefold() == key:
                raise ValueError(f'A project named "{value}" already exists. Choose a different name.')
        return value

    def _default_name(self, source: str) -> str:
        title = Path(source).stem.strip() or "Video"
        try:
            return self._validated_name(title)
        except ValueError:
            return self._validated_name(f"{title} ({self.project_id[-8:]})")

    def create(self, *, source: str, name: str = "", series_id: str = "") -> dict:
        if not source.strip():
            raise ValueError("source is required")
        name = self._validated_name(name)
        old = self.load()
        if old:
            if old.get("source") and old["source"] != source:
                raise ValueError("A project with this ID already has a different source")
            if old.get("name") and old["name"] != name:
                raise ValueError(f'This source already belongs to the project "{old["name"]}". Open that project instead.')
            return old if old.get("name") else self.rename(name, series_id)
        now = _now()
        data = {
            "schema_version": 2, "project_id": self.project_id,
            "source": source, "name": name, "series_id": series_id,
            "output_dir": str(self.output_dir), "status": "ready", "stage": "ready",
            "stage_title": "Ready", "progress": None, "active_job_id": None,
            "created_at": now, "updated_at": now, "config": {},
            "artifacts": {}, "subtitles": {}, "dubs": [], "warnings": [],
            "analysis_revision": 0, "analysis_fingerprints": {},
            "last_error": None, "runs": [],
        }
        _atomic_write(self.manifest_path, data)
        return data

    def rename(self, name: str, series_id: str) -> dict:
        payload = self.load()
        if not payload:
            raise FileNotFoundError(self.project_id)
        payload["name"] = self._validated_name(name)
        payload["series_id"] = series_id.strip()
        payload["updated_at"] = _now()
        _atomic_write(self.manifest_path, payload)
        return payload

    def begin(self, *, job_id: str, kind: str, config: Dict[str, Any], dub_id: str = "", name: str = "", retry: bool = False) -> dict:
        now = _now()
        old = self.load()
        if old.get("source") and old["source"] != str(config.get("source") or ""):
            raise ValueError("A different source video already uses this project ID in this output folder. Choose a different output folder.")
        created_at = old.get("created_at") or now
        runs = list(old.get("runs") or [])
        runs.append({
            "job_id": job_id,
            "kind": kind,
            "started_at": now,
            "status": "running",
        })
        runs = runs[-20:]

        payload = {
            "schema_version": 2,
            "project_id": self.project_id,
            "source": str(config.get("source") or ""),
            "series_id": str(config.get("series_id") or old.get("series_id") or ""),
            "name": old.get("name") or self._default_name(str(config.get("source") or "")),
            "output_dir": str(self.output_dir),
            "status": "running",
            "stage": "preparing",
            "stage_title": "Preparing",
            "progress": None,
            "active_job_id": job_id,
            "active_pid": os.getpid(),
            "created_at": created_at,
            "updated_at": now,
            "config": _redacted_config(config),
            "artifacts": dict(old.get("artifacts") or {}),
            "analysis_revision": old.get("analysis_revision", 0),
            "analysis_fingerprints": dict(old.get("analysis_fingerprints") or {}),
            "subtitles": dict(old.get("subtitles") or {}),
            "dubs": list(old.get("dubs") or []),
            "warnings": list(old.get("warnings") or [])[-50:],
            "last_error": None,
            "runs": runs,
        }
        if dub_id:
            existing = next((d for d in payload["dubs"] if d.get("id") == dub_id), None)
            if retry:
                if not existing:
                    raise ValueError("Cannot resume a dub that is not in the project")
                existing.update(status="running", stage="preparing", updated_at=now,
                                job_id=job_id, error=None, config=_redacted_config(config))
            else:
                if existing:
                    raise ValueError("Dub ID already exists")
                payload["dubs"].append({
                "id": dub_id, "name": name or f"{config.get('target_language', 'en').upper()} dub",
                "language": config.get("target_language", "en"),
                "source_language": config.get("source_language", "zh"),
                "status": "running", "stage": "preparing", "created_at": now,
                "updated_at": now, "job_id": job_id, "config": _redacted_config(config),
                "artifacts": {}, "warnings": [], "error": None,
                "analysis_revision": payload["analysis_revision"],
                "sync": "Speech clips start at source subtitle timestamps and are trimmed or time-stretched to their source windows.",
                })
        _atomic_write(self.manifest_path, payload)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        return payload

    def update_stage(
        self,
        *,
        stage: str,
        title: str = "",
        progress: Optional[float] = None,
    ) -> None:
        payload = self.load()
        if not payload:
            return
        payload["stage"] = stage
        if title:
            payload["stage_title"] = title
        payload["progress"] = progress
        payload["updated_at"] = _now()
        for dub in payload.get("dubs", []):
            if dub.get("job_id") == payload.get("active_job_id"):
                dub["stage"] = stage
                dub["updated_at"] = payload["updated_at"]
        _atomic_write(self.manifest_path, payload)

    def publish_artifact(self, kind: str, path: str, *, dub_id: str = "", version_id: str = "", language: str = "en") -> None:
        payload = self.load()
        if not payload:
            return
        path = str(path)
        if kind in {"source_srt", "chinese_srt"}:
            self._update_analysis_fingerprint(payload, kind, path)
        elif kind == "character_map":
            shared_map = self.output_dir / f"{self.project_id}_characters.json"
            self._update_analysis_fingerprint(payload, kind, str(shared_map if shared_map.exists() else path))
        payload.setdefault("artifacts", {})[kind] = path
        if kind in {"source_srt", "chinese_srt"}:
            payload["source_language"] = language
        if kind.endswith("_srt") or kind.endswith("_vtt") or kind == "review_report":
            source_caption = kind in {"source_srt", "source_vtt", "chinese_srt", "chinese_vtt"}
            key = f"source:{language}" if source_caption else (f"{version_id}:{language}" if version_id else language)
            subtitle = payload.setdefault("subtitles", {}).setdefault(key, {
                "language": language, "created_at": _now(), "artifacts": {},
            })
            subtitle.setdefault("artifacts", {})[kind] = path
        for dub in payload.get("dubs", []):
            if dub.get("id") == dub_id:
                dub.setdefault("artifacts", {})[kind] = path
                dub["analysis_revision"] = payload.get("analysis_revision", 0)
                if kind in {"source_srt", "chinese_srt"}:
                    dub["source_language"] = language
        payload["updated_at"] = _now()
        _atomic_write(self.manifest_path, payload)

    @staticmethod
    def _update_analysis_fingerprint(payload: dict, kind: str, path: str) -> None:
        source = Path(path)
        if not source.is_file():
            return
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        fingerprints = payload.setdefault("analysis_fingerprints", {})
        if fingerprints.get(kind) != digest:
            fingerprints[kind] = digest
            payload["analysis_revision"] = int(payload.get("analysis_revision") or 0) + 1

    def record_shared_analysis(self, kind: str, path: str) -> None:
        payload = self.load()
        if not payload:
            return
        self._update_analysis_fingerprint(payload, kind, path)
        payload["updated_at"] = _now()
        _atomic_write(self.manifest_path, payload)

    def set_duration(self, dub_id: str, seconds: float) -> None:
        payload = self.load()
        for dub in payload.get("dubs", []):
            if dub.get("id") == dub_id:
                dub["duration"] = round(seconds, 3)
                payload["updated_at"] = _now()
                _atomic_write(self.manifest_path, payload)
                return

    def append_log(self, message: str) -> None:
        text = str(message).replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        if not text:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = _now()
        with self.log_path.open("a", encoding="utf-8") as handle:
            for line in text.splitlines() or [text]:
                handle.write(f"{stamp} {line}\n")

    def add_warning(self, message: str, dub_id: str = "") -> None:
        payload = self.load()
        if not payload:
            return
        warnings = list(payload.get("warnings") or [])
        warnings.append({"timestamp": _now(), "message": str(message)})
        payload["warnings"] = warnings[-50:]
        for dub in payload.get("dubs", []):
            if dub.get("id") == dub_id:
                dub.setdefault("warnings", []).append(str(message))
        payload["updated_at"] = _now()
        _atomic_write(self.manifest_path, payload)

    def finish(
        self,
        *,
        status: str,
        artifacts: Optional[Dict[str, str]] = None,
        error: Optional[str] = None,
        dub_id: str = "",
    ) -> None:
        payload = self.load()
        if not payload:
            return
        now = _now()
        payload["status"] = status
        payload["stage"] = status
        payload["stage_title"] = status.replace("_", " ").title()
        payload["progress"] = 1.0 if status == "completed" else None
        payload["active_job_id"] = None
        payload["active_pid"] = None
        payload["updated_at"] = now
        payload["last_error"] = error
        if artifacts:
            merged = dict(payload.get("artifacts") or {})
            merged.update({str(k): str(v) for k, v in artifacts.items()})
            payload["artifacts"] = merged
        for dub in payload.get("dubs", []):
            if dub.get("id") == dub_id:
                dub.update(status=status, stage=status, updated_at=now, error=error)
                dub.setdefault("artifacts", {}).update(artifacts or {})

        runs = list(payload.get("runs") or [])
        if runs:
            runs[-1] = dict(runs[-1])
            runs[-1]["ended_at"] = now
            runs[-1]["status"] = status
            if error:
                runs[-1]["error"] = error
            payload["runs"] = runs[-20:]
        _atomic_write(self.manifest_path, payload)

    def delete_dub(self, dub_id: str) -> dict:
        payload = self.load()
        dub = next((d for d in payload.get("dubs", []) if d.get("id") == dub_id), None)
        if not dub:
            raise FileNotFoundError(f"Dub not found: {dub_id}")
        if dub_id == "legacy":
            raise ValueError("Legacy outputs cannot be deleted as an isolated version")
        if dub.get("status") == "running":
            raise ValueError("Stop the running job before deleting its dub")
        # Only remove the isolated output directory of this dub; never delete shared media/cache.
        version_root = (self.output_dir / "versions" / dub_id).resolve()
        if version_root.parent != (self.output_dir / "versions").resolve():
            raise ValueError("Invalid dub ID")
        if version_root.exists():
            shutil.rmtree(version_root)
        payload["dubs"] = [d for d in payload["dubs"] if d.get("id") != dub_id]
        payload["subtitles"] = {
            key: value for key, value in payload.get("subtitles", {}).items()
            if not key.startswith(dub_id + ":")
        }
        artifacts = dict(payload.get("artifacts") or {})
        for kind, value in list(artifacts.items()):
            if Path(str(value)).resolve().is_relative_to(version_root):
                replacement = next((other.get("artifacts", {}).get(kind)
                                    for other in reversed(payload["dubs"])
                                    if other.get("artifacts", {}).get(kind)), None)
                if replacement:
                    artifacts[kind] = replacement
                else:
                    artifacts.pop(kind)
        payload["artifacts"] = artifacts
        payload["updated_at"] = _now()
        _atomic_write(self.manifest_path, payload)
        return payload


def list_projects(output_dir: Path) -> list[dict]:
    root = Path(output_dir).expanduser().resolve() / ".anime_dubber_project" / "projects"
    rows = []
    manifest_paths = root.glob("*.json") if root.exists() else []
    for path in manifest_paths:
        try:
            data = _upgrade(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        rows.append({
            "project_id": str(data.get("project_id") or path.stem),
            "source": str(data.get("source") or ""),
            "source_language": data.get("source_language"),
            "series_id": str(data.get("series_id") or ""),
            "output_dir": str(data.get("output_dir") or Path(output_dir).expanduser()),
            "status": str(data.get("status") or "unknown"),
            "stage": str(data.get("stage") or ""),
            "stage_title": str(data.get("stage_title") or ""),
            "progress": data.get("progress"),
            "active_job_id": data.get("active_job_id"),
            "active_pid": data.get("active_pid"),
            "updated_at": str(data.get("updated_at") or ""),
            "created_at": str(data.get("created_at") or ""),
            "artifacts": dict(data.get("artifacts") or {}),
            "name": str(data.get("name") or ""),
            "subtitles": dict(data.get("subtitles") or {}),
            "dubs": list(data.get("dubs") or []),
            "warning_count": len(data.get("warnings") or []),
            "last_error": data.get("last_error"),
            "analysis_revision": data.get("analysis_revision", 0),
            "manifest_path": str(path),
        })
    known_ids = {str(row.get("project_id") or "") for row in rows}

    # Backfill projects created by v3.x before lightweight manifests existed.
    # This lets the new Projects screen immediately surface existing completed
    # work without forcing users to re-run multi-hour jobs.
    output = Path(output_dir).expanduser().resolve()
    for run_path in output.glob("*_run.json"):
        project_id = run_path.name.removesuffix("_run.json")
        if project_id in known_ids:
            continue
        try:
            metadata = json.loads(run_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}
        artifacts: Dict[str, str] = {}
        candidates = {
            "dubbed_video": output / f"{project_id}_EN_DUB.mp4",
            "english_srt": output / f"{project_id}_en.srt",
            "chinese_srt": output / f"{project_id}_zh.srt",
            "character_map": output / f"{project_id}_characters.json",
        }
        for kind, path in candidates.items():
            if path.exists():
                artifacts[kind] = str(path)
        modified = datetime.fromtimestamp(run_path.stat().st_mtime, tz=timezone.utc).isoformat()
        rows.append({
            "project_id": project_id,
            "source": str(metadata.get("source") or ""),
            "series_id": str(metadata.get("series_id") or ""),
            "output_dir": str(output),
            "status": "completed",
            "stage": "completed",
            "stage_title": "Completed",
            "progress": 1.0,
            "updated_at": modified,
            "created_at": modified,
            "artifacts": artifacts,
            "name": "", "subtitles": {}, "dubs": [{
                "id": "legacy", "name": "English — Legacy", "language": "en",
                "source_language": "zh", "status": "completed", "created_at": modified,
                "updated_at": modified, "config": metadata, "artifacts": artifacts,
                "warnings": [], "error": None,
            }] if "dubbed_video" in artifacts else [],
            "warning_count": 0,
            "last_error": None,
            "manifest_path": "",
            "legacy": True,
        })

    rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    return rows


def get_project(output_dir: Path, project_id: str) -> dict:
    if not project_id or Path(project_id).name != project_id:
        raise ValueError("Invalid project ID")
    root = Path(output_dir).expanduser().resolve() / ".anime_dubber_project" / "projects"
    path = root / f"{project_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Project not found: {project_id}")
    data = _upgrade(json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid project manifest: {path}")
    log_path = path.parent.parent / "logs" / f"{project_id}.log"
    data["manifest_path"] = str(path)
    data["log_path"] = str(log_path)
    return data
