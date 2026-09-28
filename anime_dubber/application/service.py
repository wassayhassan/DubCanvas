from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import uuid
from urllib.parse import urlparse
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..core import (
    CancelledError,
    CommandRunner,
    Config,
    PipelineError,
    ReviewRequired,
    DEFAULT_CONTEXT,
    DEFAULT_GLOSSARY,
    analyze_only,
    list_macos_voices,
    run_pipeline,
    _atomic_json_write,
    is_url,
    validate_media_streams,
    resolve_translation_mode,
)
from ..languages import TARGET_LANGUAGES
from .events import AppEvent, progress_to_event
from .jobs import JobRecord
from .project import ProjectStore, get_project as load_project, list_projects as list_project_manifests


EventSink = Callable[[AppEvent], None]


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def config_from_dict(payload: Dict[str, Any]) -> Config:
    """Build the current pipeline Config from the versioned UI/CLI schema.

    Both nested v4-style configuration and the existing flat Config shape are
    accepted so the backend can evolve without breaking cached callers.
    """
    data = dict(payload or {})
    audio = dict(data.get("audio") or {})
    speaker = dict(data.get("speaker_analysis") or {})
    tts = data.get("tts")
    tts = dict(tts) if isinstance(tts, dict) else {}
    translation = data.get("translation")
    translation = dict(translation) if isinstance(translation, dict) else {}
    asr = data.get("asr")
    asr = dict(asr) if isinstance(asr, dict) else {}

    source = str(data.get("source") or "").strip()
    output_dir = str(data.get("output_dir") or "").strip()
    if not source:
        raise ValueError("source is required")
    if not output_dir:
        raise ValueError("output_dir is required")
    target_language = str(data.get("target_language") or "en").lower()
    if target_language not in TARGET_LANGUAGES:
        raise ValueError("Supported target languages: " + ", ".join(TARGET_LANGUAGES))
    source_language = str(data.get("source_language") or "auto").lower()
    if source_language != "auto" and (not source_language.isalpha() or len(source_language) not in (2, 3)):
        raise ValueError("Source language must be Auto or a language code such as zh, ja, ko, es, or en")

    asr_provider = str(asr.get("provider") or data.get("asr_provider") or "auto")

    translation_provider = str(
        translation.get("provider") or data.get("translation") or "auto"
    )
    if translation_provider in {"mlx_llm", "local_llm"}:
        translation_provider = "llm"
    elif translation_provider in {"whisper_direct", "whisper_translate"}:
        translation_provider = "whisper"

    tts_provider = str(tts.get("provider") or data.get("tts_engine") or "auto")
    if tts_provider == "macos_say":
        tts_provider = "macos"

    raw_voice_overrides = data.get("voice_overrides") or {}
    if not isinstance(raw_voice_overrides, dict):
        raise ValueError("Voice overrides must be a speaker-to-settings mapping")
    allowed_voice_fields = {"tts_provider", "macos_voice", "kokoro_voice", "reference_audio", "elevenlabs_voice_id"}
    voice_overrides = {}
    for speaker_id, settings in raw_voice_overrides.items():
        if not isinstance(speaker_id, str) or not isinstance(settings, dict):
            raise ValueError("Voice overrides require speaker IDs and settings")
        voice_overrides[speaker_id] = {key: value for key, value in settings.items()
                                       if key in allowed_voice_fields and isinstance(value, str)}

    threshold = speaker.get("threshold", data.get("speaker_threshold", 0.0))
    if threshold is None:
        threshold = 0.0

    return Config(
        source=source,
        output_dir=Path(output_dir).expanduser(),
        mode=str(data.get("mode") or "dub"),
        target_language=target_language,
        source_language=source_language,
        asr_provider=asr_provider,
        mlx_whisper_model=str(asr.get("mlx_model", data.get("mlx_whisper_model", "mlx-community/whisper-large-v3-mlx")) or "mlx-community/whisper-large-v3-mlx"),
        faster_whisper_model=str(asr.get("model", data.get("faster_whisper_model", "large-v3")) or "large-v3"),
        faster_whisper_device=str(asr.get("device", data.get("faster_whisper_device", "auto")) or "auto"),
        faster_whisper_compute_type=str(asr.get("compute_type", data.get("faster_whisper_compute_type", "auto")) or "auto"),
        translation=translation_provider,
        llm_model=str(translation.get("llm_model", data.get("llm_model", "mlx-community/Qwen3-8B-4bit")) or "mlx-community/Qwen3-8B-4bit"),
        ollama_url=str(translation.get("ollama_url", data.get("ollama_url", "http://127.0.0.1:11434")) or "http://127.0.0.1:11434"),
        ollama_model=str(translation.get("model", data.get("ollama_model", "qwen3:4b")) or "qwen3:4b"),
        tts_engine=tts_provider,
        voice=str(tts.get("fallback_voice", data.get("voice", "")) or ""),
        chatterbox_reference_audio=str(
            tts.get("chatterbox_reference_audio", data.get("chatterbox_reference_audio", "")) or ""
        ),
        auto_voice_references=bool(tts.get("auto_voice_references", data.get("auto_voice_references", True))),
        chatterbox_expressiveness=float(
            tts.get("chatterbox_expressiveness", data.get("chatterbox_expressiveness", 0.5))
        ),
        chatterbox_device=str(
            tts.get("chatterbox_device", data.get("chatterbox_device", "auto")) or "auto"
        ),
        chatterbox_turbo=bool(
            tts.get("chatterbox_turbo", data.get("chatterbox_turbo", True))
        ),
        prefer_american_accent=bool(tts.get("prefer_american_accent", data.get("prefer_american_accent", True))),
        kokoro_voice=str(tts.get("kokoro_voice", data.get("kokoro_voice", "auto")) or "auto"),
        kokoro_language=str(tts.get("kokoro_language", data.get("kokoro_language", "a")) or "a"),
        piper_model=str(tts.get("piper_model", data.get("piper_model", "")) or ""),
        piper_speaker=int(tts.get("piper_speaker", data.get("piper_speaker", -1))),
        tts_rate=int(tts.get("rate", data.get("tts_rate", 210))),
        context=str(data.get("context") or data.get("series_context") or DEFAULT_CONTEXT),
        glossary=dict(DEFAULT_GLOSSARY if data.get("glossary") is None and source_language == "zh"
                      else data.get("glossary") or {}),
        keep_work=bool(data.get("keep_work", True)),
        resume=bool(data.get("resume", True)),
        force=bool(data.get("force", False)),
        demucs_device=str(data.get("demucs_device") or "auto"),
        chunk_seconds=int(data.get("chunk_seconds", 120)),
        background_volume=float(audio.get("background_volume", data.get("background_volume", 1.0))),
        dub_volume=float(audio.get("dub_volume", data.get("dub_volume", 1.15))),
        ducking=bool(audio.get("ducking", data.get("ducking", False))),
        elevenlabs_api_key=str(tts.get("api_key", data.get("elevenlabs_api_key", "")) or ""),
        elevenlabs_voice_id=str(tts.get("voice_id", data.get("elevenlabs_voice_id", "JBFqnCBsd6RMkjVDRZzb")) or ""),
        elevenlabs_model_id=str(tts.get("model_id", data.get("elevenlabs_model_id", "eleven_v3")) or ""),
        voice_overrides=voice_overrides,
        multi_character=bool(speaker.get("enabled", data.get("multi_character", True))),
        max_speakers=max(2, int(speaker.get("max_speakers", data.get("max_speakers", 12)))),
        speaker_threshold=max(0.0, float(threshold)),
        series_id=str(data.get("series_id") or ""),
        speaker_backend=str(speaker.get("backend", data.get("speaker_backend", "auto")) or "auto"),
        review_before_dub=bool(data.get("review_before_dub", True)),
        review_model=str(data.get("review_model") or "mlx-community/Qwen3-8B-4bit"),
    )


class ApplicationService:
    def __init__(self, event_sink: Optional[EventSink] = None):
        self._event_sink = event_sink or (lambda _event: None)
        self._jobs: Dict[str, JobRecord] = {}
        self._project_stores: Dict[str, ProjectStore] = {}
        self._project_progress_buckets: Dict[str, int] = {}
        self._lock = threading.RLock()

    def _emit(self, event: AppEvent) -> None:
        self._event_sink(event)

    def capabilities(self) -> dict:
        system = platform.system()
        machine = platform.machine()
        mlx_ok = system == "Darwin" and machine == "arm64" and _module_available("mlx_whisper")
        mlx_lm_ok = system == "Darwin" and machine == "arm64" and _module_available("mlx_lm")
        faster_ok = _module_available("faster_whisper")
        piper_ok = bool(shutil.which("piper")) or _module_available("piper")
        chatterbox_ok = _module_available("chatterbox") and _module_available("torchaudio")
        multilingual_ok = chatterbox_ok and _module_available("chatterbox.mtl_tts")
        kokoro_ok = _module_available("kokoro") and _module_available("soundfile")
        ollama_ok = bool(shutil.which("ollama"))

        return {
            "platform": {"system": system, "machine": machine, "python": sys.version.split()[0]},
            "providers": {
                "asr": {
                    "mlx_whisper": mlx_ok,
                    "faster_whisper": faster_ok,
                },
                "translation": {
                    "mlx_llm": mlx_lm_ok,
                    "whisper_direct": mlx_ok or faster_ok,
                    "ollama": ollama_ok,
                },
                "tts": {
                    "chatterbox": chatterbox_ok,
                    "chatterbox_multilingual": multilingual_ok,
                    "kokoro": kokoro_ok,
                    "macos": system == "Darwin" and bool(shutil.which("say")),
                    "piper": piper_ok,
                    "elevenlabs": True,
                },
                "stems": {
                    "demucs": _module_available("demucs"),
                },
            },
            "current_pipeline": {
                "engine": "v4_shared",
                "processing_supported": bool(
                    _module_available("demucs") and (mlx_ok or faster_ok)
                ),
                "note": (
                    "Premium local voices prefer Chatterbox Turbo, then Kokoro when installed. "
                    "Apple silicon can also use MLX Whisper/MLX LLM/macOS voices; "
                    "Windows and Linux can use Faster-Whisper, Whisper-direct or Ollama translation, "
                    "plus Chatterbox/Kokoro/Piper or ElevenLabs TTS."
                ),
            },
        }

    def system_check(self) -> dict:
        caps = self.capabilities()
        checks = []
        for exe in ("ffmpeg", "ffprobe"):
            path = shutil.which(exe)
            checks.append({"name": exe, "ok": bool(path), "detail": path or "not found"})
        yt = shutil.which("yt-dlp") or shutil.which("yt_dlp")
        yt_module = _module_available("yt_dlp")
        checks.append({
            "name": "yt-dlp",
            "ok": bool(yt or yt_module),
            "detail": yt or ("Python module available" if yt_module else "not found"),
        })
        checks.append({
            "name": "demucs",
            "ok": bool(caps["providers"]["stems"]["demucs"]),
            "detail": "available" if caps["providers"]["stems"]["demucs"] else "not installed",
        })

        if platform.system() == "Darwin":
            checks.append({
                "name": "mlx-whisper",
                "ok": bool(caps["providers"]["asr"]["mlx_whisper"]),
                "detail": "available" if caps["providers"]["asr"]["mlx_whisper"] else "not installed",
            })
            checks.append({
                "name": "Chatterbox Turbo",
                "ok": bool(caps["providers"]["tts"]["chatterbox"]),
                "detail": "available" if caps["providers"]["tts"]["chatterbox"] else "optional; run macos/install_voice_engines.sh",
                "optional": True,
            })
            checks.append({
                "name": "Chatterbox Multilingual",
                "ok": bool(caps["providers"]["tts"]["chatterbox_multilingual"]),
                "detail": "available" if caps["providers"]["tts"]["chatterbox_multilingual"] else "optional; run macos/install_voice_engines.sh",
                "optional": True,
            })
            checks.append({
                "name": "Kokoro",
                "ok": bool(caps["providers"]["tts"]["kokoro"]),
                "detail": "available" if caps["providers"]["tts"]["kokoro"] else "optional; run macos/install_voice_engines.sh",
                "optional": True,
            })
            checks.append({
                "name": "macOS voices",
                "ok": bool(caps["providers"]["tts"]["macos"]),
                "detail": "available" if caps["providers"]["tts"]["macos"] else "say not found",
            })
        else:
            checks.append({
                "name": "faster-whisper",
                "ok": bool(caps["providers"]["asr"]["faster_whisper"]),
                "detail": "available" if caps["providers"]["asr"]["faster_whisper"] else "install requirements-cross-platform.txt",
            })
            checks.append({
                "name": "Chatterbox Turbo",
                "ok": bool(caps["providers"]["tts"]["chatterbox"]),
                "detail": "available" if caps["providers"]["tts"]["chatterbox"] else "optional premium local TTS",
                "optional": True,
            })
            checks.append({
                "name": "Kokoro",
                "ok": bool(caps["providers"]["tts"]["kokoro"]),
                "detail": "available" if caps["providers"]["tts"]["kokoro"] else "optional fast local TTS",
                "optional": True,
            })
            checks.append({
                "name": "Piper",
                "ok": bool(caps["providers"]["tts"]["piper"]),
                "detail": "available (voice model still required)" if caps["providers"]["tts"]["piper"] else "optional local TTS; install piper-tts or use ElevenLabs",
                "optional": True,
            })
            checks.append({
                "name": "Ollama",
                "ok": bool(caps["providers"]["translation"]["ollama"]),
                "detail": "available" if caps["providers"]["translation"]["ollama"] else "optional; Whisper direct translation works without it",
                "optional": True,
            })

        required = [x for x in checks if not x.get("optional")]
        base_ok = all(x["ok"] for x in required)
        return {"ok": base_ok, "checks": checks, "capabilities": caps}

    def list_voices(self) -> list[str]:
        if platform.system() != "Darwin":
            return []
        return list_macos_voices()

    def _normalized_config_dict(self, config: Config) -> dict:
        data = _json_safe(asdict(config))
        if data.get("elevenlabs_api_key"):
            data["elevenlabs_api_key"] = "<redacted>"
        return data

    def validate_job_setup(self, config: Config, *, analysis: bool = False) -> None:
        """Reject missing selected providers before a long job creates a version."""
        from ..providers.asr import resolve_asr_provider
        caps = self.capabilities()["providers"]
        missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
        if (analysis or config.mode == "dub") and not caps["stems"]["demucs"]:
            missing.append("Demucs dialogue separation")
        selected_asr = resolve_asr_provider(config.asr_provider)
        if not caps["asr"].get(selected_asr, False):
            missing.append(f"{selected_asr} speech recognition")
        if not analysis and config.source_language != config.target_language:
            try:
                provider = resolve_translation_mode(
                    config,
                    mlx_available=bool(caps["translation"]["mlx_llm"]),
                    ollama_available=bool(caps["translation"]["ollama"]),
                )
            except PipelineError:
                provider = "unavailable"
            if provider == "llm" and not caps["translation"]["mlx_llm"]:
                missing.append("MLX translation model runtime")
            elif provider == "ollama" and not caps["translation"]["ollama"]:
                missing.append("Ollama translation runtime")
            elif provider == "unavailable":
                missing.append("translation model runtime")
        if config.mode == "dub" and not analysis:
            voices = caps["tts"]
            if config.target_language != "en" and config.tts_engine in {"auto", "chatterbox"}:
                if not voices["chatterbox_multilingual"] and not (
                    config.tts_engine == "auto" and config.elevenlabs_api_key
                ):
                    missing.append("Chatterbox Multilingual or ElevenLabs API key")
            elif config.tts_engine not in {"auto", "elevenlabs"} and not voices.get(config.tts_engine):
                missing.append(f"{config.tts_engine} voice engine")
            if config.tts_engine == "elevenlabs" and not config.elevenlabs_api_key:
                missing.append("ElevenLabs API key")
            if config.target_language == "en" and config.tts_engine == "auto" and not (
                voices["chatterbox"] or voices["kokoro"] or voices["macos"] or
                (voices["piper"] and config.piper_model) or config.elevenlabs_api_key
            ):
                missing.append("usable English voice engine")
        if missing:
            raise ValueError("Setup needed before processing: " + ", ".join(missing))
        if not is_url(config.source):
            source_file = Path(config.source).expanduser()
            if not source_file.is_file():
                raise ValueError("Source video is missing. Choose the file again before processing.")
            validate_media_streams(source_file, CommandRunner())

    @staticmethod
    def _check_external_job(project: dict) -> None:
        if project.get("status") != "running":
            return
        pid = project.get("active_pid")
        if not pid or pid == os.getpid():
            return
        try:
            os.kill(int(pid), 0)
        except (ProcessLookupError, ValueError):
            return
        except PermissionError:
            pass
        raise ValueError("Another process is processing this project")

    def start_job(self, payload: Dict[str, Any], *, analysis: bool = False) -> str:
        config = config_from_dict(payload)
        if payload.get("verify_setup"):
            self.validate_job_setup(config, analysis=analysis)
        store = ProjectStore(config.output_dir, config.source)
        if not config.series_id:
            config.series_id = str(store.load().get("series_id") or "")
        if config.mode == "dub" and config.target_language != "en" and config.tts_engine not in {"auto", "chatterbox", "elevenlabs"}:
            raise ValueError("This target language needs Chatterbox Multilingual or ElevenLabs.")
        if config.translation == "whisper" and config.target_language != "en" and config.source_language not in {"auto", config.target_language}:
            raise ValueError("Whisper direct translation only supports English; choose an LLM or Ollama.")
        job_id = "job_" + uuid.uuid4().hex[:12]
        dub_id = "dub_" + uuid.uuid4().hex[:12] if not analysis and config.mode == "dub" else ""
        config.version_id = dub_id or ("sub_" + uuid.uuid4().hex[:12] if not analysis else "")
        record = JobRecord(
            id=job_id,
            kind="analyze" if analysis else "run",
            config=self._normalized_config_dict(config),
        )
        record.runner = CommandRunner()
        with self._lock:
            self._check_external_job(store.load())
            if any(s.project_id == store.project_id and s.output_dir == store.output_dir
                   and self._jobs[j].status in {"queued", "running"}
                   for j, s in self._project_stores.items()):
                raise ValueError("A job is already running for this project")
            store.begin(job_id=job_id, kind=record.kind, config=record.config,
                        dub_id=dub_id, name=str(payload.get("dub_name") or ""))
            self._jobs[job_id] = record
            self._project_stores[job_id] = store

        thread = threading.Thread(
            target=self._execute,
            args=(record, config, analysis),
            name=f"DubCanvas-{job_id}",
            daemon=True,
        )
        thread.start()
        return job_id

    def run_sync(self, payload: Dict[str, Any], *, analysis: bool = False) -> dict:
        config = config_from_dict(payload)
        store = ProjectStore(config.output_dir, config.source)
        if not config.series_id:
            config.series_id = str(store.load().get("series_id") or "")
        if config.mode == "dub" and config.target_language != "en" and config.tts_engine not in {"auto", "chatterbox", "elevenlabs"}:
            raise ValueError("This target language needs Chatterbox Multilingual or ElevenLabs.")
        if config.translation == "whisper" and config.target_language != "en" and config.source_language not in {"auto", config.target_language}:
            raise ValueError("Whisper direct translation only supports English; choose an LLM or Ollama.")
        job_id = "job_" + uuid.uuid4().hex[:12]
        dub_id = "dub_" + uuid.uuid4().hex[:12] if not analysis and config.mode == "dub" else ""
        config.version_id = dub_id or ("sub_" + uuid.uuid4().hex[:12] if not analysis else "")
        record = JobRecord(
            id=job_id,
            kind="analyze" if analysis else "run",
            config=self._normalized_config_dict(config),
        )
        with self._lock:
            self._check_external_job(store.load())
            if any(s.project_id == store.project_id and s.output_dir == store.output_dir
                   and self._jobs[j].status in {"queued", "running"}
                   for j, s in self._project_stores.items()):
                raise ValueError("A job is already running for this project")
            store.begin(job_id=job_id, kind=record.kind, config=record.config,
                        dub_id=dub_id, name=str(payload.get("dub_name") or ""))
            self._jobs[job_id] = record
            self._project_stores[job_id] = store
        self._execute(record, config, analysis)
        snapshot = self.get_job(job_id)
        if snapshot["status"] == "failed":
            raise RuntimeError(snapshot["error"] or "DubCanvas job failed")
        if snapshot["status"] == "cancelled":
            raise CancelledError("Cancelled by user")
        return snapshot

    def resume_dub(self, output_dir: str, project_id: str, dub_id: str, *, api_key: str = "") -> str:
        """Retry an interrupted version with its original settings and cache directory."""
        project = self.get_project(output_dir, project_id)
        dub = next((d for d in project.get("dubs", []) if d.get("id") == dub_id), None)
        if not dub or dub_id == "legacy":
            raise ValueError("No resumable dub with that ID")
        if dub.get("status") not in {"paused", "cancelled", "failed", "running"}:
            raise ValueError("Only interrupted or failed dubs can be resumed")
        saved = dict(dub.get("config") or {})
        if saved.get("version_id") != dub_id or saved.get("source") != project.get("source"):
            raise ValueError("Dub settings do not match the original project")
        if Path(str(saved.get("output_dir"))).expanduser().resolve() != Path(output_dir).expanduser().resolve():
            raise ValueError("Dub output folder has changed")
        if saved.get("keep_work") is False:
            raise ValueError("This dub was configured to discard processing files and cannot resume")
        if saved.get("elevenlabs_api_key") == "<redacted>":
            if not api_key:
                raise ValueError("Enter the ElevenLabs key in Settings before resuming this dub")
            saved["elevenlabs_api_key"] = api_key
        # Older dub versions started without automatic source voices. Preserve
        # their original voice choice when a failed job resumes after upgrade.
        saved.setdefault("auto_voice_references", False)
        saved.update(resume=True, force=False, keep_work=True)
        config = config_from_dict(saved)
        config.version_id = dub_id
        store = ProjectStore(config.output_dir, config.source)
        if store.project_id != project_id:
            raise ValueError("Project source does not match its manifest")
        job_id = "job_" + uuid.uuid4().hex[:12]
        record = JobRecord(id=job_id, kind="run", config=self._normalized_config_dict(config))
        record.runner = CommandRunner()
        with self._lock:
            if any(s.project_id == project_id and s.output_dir == store.output_dir
                   and self._jobs[j].status in {"queued", "running"}
                   for j, s in self._project_stores.items()):
                raise ValueError("A job is already running for this project")
            # A manifest can say running after a crash. Never take over a live backend.
            self._check_external_job(store.load())
            store.begin(job_id=job_id, kind="run", config=record.config, dub_id=dub_id, retry=True)
            self._jobs[job_id] = record
            self._project_stores[job_id] = store
        threading.Thread(target=self._execute, args=(record, config, False),
                         name=f"DubCanvas-{job_id}", daemon=True).start()
        return job_id

    def approve_review(self, output_dir: str, project_id: str, dub_id: str,
                       revisions: Dict[str, str]) -> dict:
        """Record an explicit review decision for one paused dub version."""
        project = self.get_project(output_dir, project_id)
        dub = next((item for item in project.get("dubs", []) if item.get("id") == dub_id), None)
        if not dub or dub.get("status") != "paused" or dub.get("error") != "Subtitle review required before voice generation":
            raise ValueError("This dub is not awaiting subtitle review")
        path = Path(str(dub.get("artifacts", {}).get("review_report") or "")).resolve()
        version_dir = (Path(output_dir).expanduser().resolve() / "versions" / dub_id).resolve()
        if path.parent != version_dir or not path.name.endswith(".review.json"):
            raise ValueError("Review report is not in this dub version")
        report = json.loads(path.read_text(encoding="utf-8"))
        permitted = set(report.get("priority_cues") or [])
        cleaned = {}
        for key, value in revisions.items():
            cue = int(key)
            if cue not in permitted or not isinstance(value, str) or not value.strip():
                raise ValueError("Review revision must name a priority cue and contain text")
            cleaned[str(cue)] = value.strip()
        for cue, issue in report.get("timing_issues", {}).items():
            if cleaned.get(str(cue)) in {None, issue.get("attempted_translation")}:
                raise ValueError(f"Line {cue} overlaps the next voice. Enter a shorter translation before continuing.")
        approval = path.with_name(path.name.replace(".review.json", ".review-approval.json"))
        if approval.exists():
            previous = json.loads(approval.read_text(encoding="utf-8"))
            if previous.get("signature") == report["signature"]:
                cleaned = {**previous.get("revisions", {}), **cleaned}
        _atomic_json_write(approval, {"signature": report["signature"], "revisions": cleaned})
        return {"approved": True, "revisions": len(cleaned), "path": str(approval)}

    def _execute(self, record: JobRecord, config: Config, analysis: bool) -> None:
        from datetime import datetime, timezone

        record.status = "running"
        record.stage = "preparing"
        record.started_at = datetime.now(timezone.utc).isoformat()
        runner = record.runner or CommandRunner()
        record.runner = runner
        store = self._project_stores.get(record.id)

        def progress(message: str) -> None:
            if str(message).lower().startswith("warning"):
                if store:
                    store.add_warning(str(message), dub_id=config.version_id)
                self._emit(AppEvent("warning", {"message": str(message)}, record.id))
            event = progress_to_event(message, record.id)
            if event.event in {"stage", "progress"}:
                stage = str(event.data.get("stage") or record.stage)
                record.stage = stage
                if store:
                    fraction = event.data.get("fraction")
                    should_write = event.event == "stage"
                    if isinstance(fraction, (int, float)):
                        bucket = int(max(0.0, min(1.0, float(fraction))) * 20)
                        old_bucket = self._project_progress_buckets.get(record.id)
                        if old_bucket != bucket:
                            self._project_progress_buckets[record.id] = bucket
                            should_write = True
                    if should_write:
                        store.update_stage(
                            stage=stage,
                            title=str(event.data.get("title") or ""),
                            progress=float(fraction) if isinstance(fraction, (int, float)) else None,
                        )
            self._emit(event)
            if not str(message).startswith("__DOWNLOAD_PROGRESS__|"):
                if store:
                    store.append_log(str(message))
                self._emit(AppEvent("log", {"level": "info", "message": str(message)}, record.id))

        runner.progress = progress
        def publish(kind: str, path: Path, language: str = "") -> None:
            if store:
                store.publish_artifact(kind, str(path), dub_id=config.version_id, version_id=config.version_id,
                                       language=language or config.target_language)
            self._emit(AppEvent("artifact", {"kind": kind, "path": str(path)}, record.id))
        runner.artifact = publish
        runner.source_changed = store.invalidate_shared_source if store else lambda: None
        runner.duration = lambda seconds: store.set_duration(config.version_id, seconds) if store and config.version_id else None
        self._emit(AppEvent("job_started", {"kind": record.kind}, record.id))

        try:
            # The application boundary is cross-platform now, while the legacy v3
            # implementation underneath it is still MLX/macOS-specific. Provider
            # replacement happens without changing the SwiftUI/CLI contracts.
            results = analyze_only(config, progress, runner) if analysis else run_pipeline(config, progress, runner)
            runner.check_cancel()
            record.result = {str(k): str(v) for k, v in results.items()}
            if store:
                store.finish(status="completed", artifacts=record.result, dub_id=config.version_id)
            record.status = "completed"
            record.stage = "completed"
            for kind, path in record.result.items():
                self._emit(AppEvent("artifact", {"kind": kind, "path": path}, record.id))
            self._emit(AppEvent("finished", {"status": "completed", "result": record.result}, record.id))
        except CancelledError as exc:
            record.status = "paused" if runner.pause_requested or isinstance(exc, ReviewRequired) else "cancelled"
            record.stage = record.status
            record.error = str(exc)
            if store:
                store.finish(status=record.status, error=str(exc), dub_id=config.version_id)
            self._emit(AppEvent("finished", {"status": record.status}, record.id))
        except Exception as exc:
            if runner.cancel_event.is_set():
                record.status = "paused" if runner.pause_requested else "cancelled"
                record.stage = record.status
                record.error = "Paused by user" if runner.pause_requested else "Cancelled by user"
                if store:
                    store.finish(status=record.status, error=record.error, dub_id=config.version_id)
                self._emit(AppEvent("finished", {"status": record.status}, record.id))
                return
            record.status = "failed"
            record.stage = "failed"
            record.error = str(exc)
            if store:
                store.finish(status="failed", error=str(exc), dub_id=config.version_id)
            self._emit(AppEvent("error", {"message": str(exc), "error_type": type(exc).__name__}, record.id))
            self._emit(AppEvent("finished", {"status": "failed"}, record.id))
        finally:
            record.ended_at = datetime.now(timezone.utc).isoformat()
            record.runner = None
            with self._lock:
                self._project_progress_buckets.pop(record.id, None)

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
        if not record:
            return False
        if record.runner:
            record.runner.cancel()
            return True
        return record.status in {"cancelled", "completed", "failed"}

    def pause_job(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record or record.status not in {"queued", "running"}:
                return False
            if record.runner:
                record.runner.cancel(pause=True)
                return True
        return False

    def get_job(self, job_id: str) -> dict:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                raise KeyError(job_id)
            return record.to_dict()

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [job.to_dict() for job in self._jobs.values()]

    def list_projects(self, output_dir: str) -> list[dict]:
        if not str(output_dir or "").strip():
            raise ValueError("output_dir is required")
        root = Path(output_dir).expanduser()
        projects = list_project_manifests(root)
        recovered = False
        for project in projects:
            recovered = self._recover_interrupted_project(root, project) or recovered
        if recovered:
            return list_project_manifests(root)
        return projects

    def _recover_interrupted_project(self, output_dir: Path, project: dict) -> bool:
        """Make jobs left running by a dead backend resumable on next launch."""
        if project.get("status") != "running" or project.get("legacy"):
            return False
        job_id = project.get("active_job_id")
        with self._lock:
            if job_id in self._jobs and self._jobs[job_id].status in {"queued", "running"}:
                return False
        pid = project.get("active_pid")
        if pid:
            try:
                os.kill(int(pid), 0)
            except (ProcessLookupError, ValueError):
                pass
            except PermissionError:
                return False
            else:
                return False
        store = ProjectStore(output_dir, str(project.get("source") or ""))
        if store.project_id != project.get("project_id"):
            return False
        current = store.load()
        if current.get("status") != "running" or current.get("active_job_id") != job_id:
            return False
        dub = next((item for item in current.get("dubs", []) if item.get("job_id") == job_id), None)
        message = ("Processing stopped unexpectedly. Resume this dub to use completed work." if dub else
                   "Source analysis stopped unexpectedly. Retry it to use completed work.")
        store.finish(status="paused", error=message,
                     dub_id=str(dub.get("id") or "") if dub else "")
        return True

    def create_project(self, output_dir: str, source: str, name: str = "", series_id: str = "", source_title: str = "") -> dict:
        if not output_dir.strip() or not source.strip():
            raise ValueError("source and output_dir are required")
        return ProjectStore(Path(output_dir), source).create(source=source, name=name, series_id=series_id,
                                                          source_title=source_title)

    def inspect_source(self, source: str) -> dict:
        """Check the selected media before committing a project or starting a job."""
        value = str(source or "").strip()
        if not value:
            raise ValueError("Choose a video file or paste a video page link.")
        if is_url(value):
            parsed = urlparse(value)
            if (not parsed.hostname or parsed.hostname.endswith("googlevideo.com")
                    or "/videoplayback" in parsed.path):
                raise ValueError("Use a normal video page link. Temporary playback links expire; paste the page URL instead.")
            if not _module_available("yt_dlp"):
                raise ValueError("Video link support needs yt-dlp. Install the app's video tools or choose a local file.")
            command = [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download",
                       "--no-playlist", "--socket-timeout", "8", "--retries", "1"]
            attempts = [command + [value]]
            if parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
                attempts.append(command + ["--extractor-args", "youtube:player_client=web_embedded", value])
            checked = None
            for attempt in attempts:
                try:
                    checked = subprocess.run(attempt, capture_output=True, text=True, timeout=30, check=False)
                except subprocess.TimeoutExpired as exc:
                    if attempt is attempts[-1]:
                        raise ValueError("The video site did not respond in time. Check the link and connection, or choose a local file.") from exc
                    continue
                if checked.returncode == 0:
                    break
            if checked is None:
                raise ValueError("The video link could not be checked. Choose a local file or retry the page URL.")
            if checked.returncode != 0:
                detail = (checked.stderr or "").strip().splitlines()[-1:]
                raise ValueError("The video link could not be opened. Check the page URL or choose a local file. "
                                 + " ".join(detail)[:300])
            try:
                info = json.loads(checked.stdout)
            except (ValueError, TypeError) as exc:
                raise ValueError("The video site did not return usable details. Choose a local file or a supported video page.") from exc
            if info.get("is_live"):
                raise ValueError("Live streams cannot be dubbed. Choose a saved video instead.")
            if not info.get("formats") and not info.get("url") and not info.get("requested_formats"):
                raise ValueError("This link does not provide a downloadable video. Choose a video page or local file.")
            formats = info.get("requested_formats") or info.get("formats") or [info]
            if not any(row.get("vcodec") not in (None, "", "none") for row in formats):
                raise ValueError("No video track was found at this link. Choose a video page or a local video file.")
            if not any(row.get("acodec") not in (None, "", "none") for row in formats):
                raise ValueError("No audio track was found at this link. Choose a video with audible dialogue.")
            try:
                duration = float(info.get("duration") or 0)
            except (ValueError, TypeError):
                duration = 0
            return {"source": value, "kind": "url", "title": str(info.get("title") or parsed.hostname),
                    "duration": duration, "size_bytes": info.get("filesize") or info.get("filesize_approx"),
                    "thumbnail_url": str(info.get("thumbnail") or "")}
        if "://" in value:
            raise ValueError("Only http or https video page links are supported. Paste the page URL or choose a local file.")
        path = Path(value).expanduser()
        if not path.is_file():
            raise ValueError("The video file is missing. Choose it again from its current location.")
        try:
            with path.open("rb") as handle:
                if not handle.read(1):
                    raise ValueError("The video file is empty. Choose a different file.")
            validate_media_streams(path, CommandRunner())
            probe = CommandRunner().run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "json", str(path),
            ])
            duration = float(json.loads(probe.stdout or "{}").get("format", {}).get("duration") or 0)
        except OSError as exc:
            raise ValueError("The video file cannot be read. Check its permissions or choose another file.") from exc
        except (PipelineError, ValueError) as exc:
            raise ValueError(f"The selected file is not a usable video with audio: {exc}") from exc
        return {"source": value, "kind": "file", "title": path.name,
                "duration": duration, "size_bytes": path.stat().st_size}

    def update_project(self, output_dir: str, project_id: str, name: str, series_id: str) -> dict:
        project = self.get_project(output_dir, project_id)
        return ProjectStore(Path(output_dir), project["source"]).rename(name, series_id)

    def delete_dub(self, output_dir: str, project_id: str, dub_id: str) -> dict:
        if not output_dir or not project_id or not dub_id:
            raise ValueError("output_dir, project_id and dub_id are required")
        store = ProjectStore(Path(output_dir), "placeholder")
        if Path(project_id).name != project_id or not project_id:
            raise ValueError("Invalid project ID")
        store.project_id = project_id
        store.manifest_path = store.root / "projects" / f"{project_id}.json"
        project = store.load()
        if not project:
            raise FileNotFoundError(project_id)
        if project.get("status") == "running":
            self._check_external_job(project)
            raise ValueError("Stop the active project job before deleting a dub")
        return store.delete_dub(dub_id)

    def delete_project(self, output_dir: str, project_id: str) -> dict:
        if not str(output_dir or "").strip() or not project_id or Path(project_id).name != project_id:
            raise ValueError("A valid output_dir and project_id are required")
        store = ProjectStore(Path(output_dir), "placeholder")
        store.project_id = project_id
        store.manifest_path = store.root / "projects" / f"{project_id}.json"
        store.log_path = store.root / "logs" / f"{project_id}.log"
        with self._lock:
            if any(s.output_dir == store.output_dir and s.project_id == project_id
                   and self._jobs[j].status in {"queued", "running"}
                   for j, s in self._project_stores.items()):
                raise ValueError("Pause or stop the active job before deleting this project")
            project = store.load()
            if project.get("status") == "running":
                self._check_external_job(project)
                raise ValueError("Pause or stop the active job before deleting this project")
            return store.delete_project()

    def get_project(self, output_dir: str, project_id: str) -> dict:
        if not str(output_dir or "").strip():
            raise ValueError("output_dir is required")
        if not str(project_id or "").strip():
            raise ValueError("project_id is required")
        root = Path(output_dir).expanduser()
        project = load_project(root, str(project_id))
        if self._recover_interrupted_project(root, project):
            return load_project(root, str(project_id))
        return project

    def list_character_maps(self, output_dir: str) -> list[dict]:
        if not str(output_dir or "").strip():
            raise ValueError("output_dir is required")
        from ..characters import list_character_maps
        return list_character_maps(Path(output_dir).expanduser())

    def get_characters(self, path: str) -> dict:
        if not str(path or "").strip():
            raise ValueError("path is required")
        from ..characters import character_map_for_ui
        return character_map_for_ui(Path(path).expanduser())

    def update_character(self, path: str, character_id: str, updates: Dict[str, Any]) -> dict:
        if not str(path or "").strip():
            raise ValueError("path is required")
        if not str(character_id or "").strip():
            raise ValueError("character_id is required")
        from ..characters import update_character_override
        saved = update_character_override(Path(path).expanduser(), character_id, updates)
        character_path = Path(path).expanduser().resolve()
        key = character_path.name.removesuffix("_characters.json")
        if character_path.name.endswith("_characters.json"):
            store = ProjectStore(character_path.parent, "placeholder")
            store.project_id = key
            store.manifest_path = store.root / "projects" / f"{key}.json"
            store.record_shared_analysis("character_map", str(character_path))
        self._emit(AppEvent("character_updated", {"path": str(path), "character": saved}))
        return saved

    def preview_voice(self, settings: Dict[str, Any]) -> dict:
        import tempfile

        settings = dict(settings or {})
        provider = str(settings.get("provider") or "macos").strip().lower()
        clean_text = str(settings.get("text") or "").strip() or "This is the selected character speaking in English."
        rate = max(80, min(450, int(settings.get("rate") or 205)))

        if provider in {"", "inherit", "auto"}:
            from ..providers.tts import chatterbox_available, kokoro_available
            if chatterbox_available():
                provider = "chatterbox"
            elif kokoro_available():
                provider = "kokoro"
            elif platform.system() == "Darwin" and shutil.which("say"):
                provider = "macos"
            else:
                provider = "piper"

        # Preview uses the same no-reference fallback as dub generation.
        # Otherwise Chatterbox's single unconditioned default sounds identical
        # for every character regardless of its displayed age/voice class.
        if provider == "chatterbox" and settings.get("character_id") and not str(settings.get("reference_audio") or "").strip():
            from ..core import _character_voice_fallback
            provider = _character_voice_fallback()

        if provider == "macos":
            if platform.system() != "Darwin" or not shutil.which("say"):
                raise RuntimeError("macOS voice preview requires the 'say' command.")
            cmd = ["say", "-r", str(rate)]
            clean_voice = str(settings.get("voice") or "").strip()
            if clean_voice:
                cmd += ["-v", clean_voice]
            cmd.append(clean_text)
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"started": True, "provider": provider}

        suffix = ".mp3" if provider == "elevenlabs" else ".wav"
        preview_path = Path(tempfile.gettempdir()) / f"animedubber_preview_{uuid.uuid4().hex}{suffix}"
        runner = CommandRunner()

        if provider == "chatterbox":
            from ..providers.tts import synthesize_chatterbox
            synthesize_chatterbox(
                clean_text,
                preview_path,
                reference_audio=str(settings.get("reference_audio") or ""),
                expressiveness=float(settings.get("expressiveness") or 0.5),
                device=str(settings.get("device") or "auto"),
                turbo=bool(settings.get("turbo", True)),
                american_english=bool(settings.get("prefer_american_accent", True)
                                      and settings.get("source_language", "zh") != "en"),
                cancel_check=runner.check_cancel,
            )
        elif provider == "kokoro":
            from ..providers.tts import automatic_kokoro_voice, synthesize_kokoro
            selected_kokoro = str(settings.get("kokoro_voice") or "auto")
            if selected_kokoro == "auto":
                selected_kokoro = automatic_kokoro_voice({
                    "voice_class": settings.get("voice_class") or "neutral",
                    "age_group": settings.get("age_group") or "adult",
                })
            synthesize_kokoro(
                clean_text,
                preview_path,
                voice=selected_kokoro,
                rate=rate,
                lang_code=str(settings.get("kokoro_language") or "a"),
                cancel_check=runner.check_cancel,
            )
        elif provider == "piper":
            from ..providers.tts import synthesize_piper
            synthesize_piper(
                clean_text,
                preview_path,
                model_path=str(settings.get("piper_model") or ""),
                rate=rate,
                speaker=(
                    int(settings.get("piper_speaker"))
                    if settings.get("piper_speaker") is not None and int(settings.get("piper_speaker")) >= 0
                    else None
                ),
                cancel_check=runner.check_cancel,
            )
        elif provider == "elevenlabs":
            cfg = Config(
                source="preview",
                output_dir=preview_path.parent,
                tts_engine="elevenlabs",
                elevenlabs_api_key=str(settings.get("api_key") or ""),
                elevenlabs_voice_id=str(settings.get("voice_id") or "JBFqnCBsd6RMkjVDRZzb"),
                elevenlabs_model_id=str(settings.get("model_id") or "eleven_v3"),
            )
            from ..core import synthesize_elevenlabs
            synthesize_elevenlabs(clean_text, preview_path, cfg, runner)
        else:
            raise RuntimeError(f"Unsupported preview provider: {provider}")

        if platform.system() == "Darwin" and shutil.which("afplay"):
            subprocess.Popen(["afplay", str(preview_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"started": True, "provider": provider, "path": str(preview_path)}
