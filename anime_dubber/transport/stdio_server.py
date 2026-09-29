from __future__ import annotations

import json
import sys
import threading
from typing import Any, Dict

from .. import __version__
from ..application import ApplicationService
from .protocol import decode_request, encode_message, response_error, response_ok
from ..application.diagnostics import DiagnosticStream


class JsonLineWriter:
    def __init__(self, stream):
        self._stream = stream
        self._lock = threading.Lock()

    def write(self, payload: Dict[str, Any]) -> None:
        line = encode_message(payload)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()


def serve() -> int:
    # Reserve the original stdout exclusively for JSON protocol messages.
    # Third-party ML libraries occasionally print diagnostics; send incidental
    # Python stdout to stderr so one stray print cannot corrupt the JSONL stream.
    protocol_stdout = sys.stdout
    writer = JsonLineWriter(protocol_stdout)
    sys.stderr = DiagnosticStream(sys.stderr)
    sys.stdout = sys.stderr

    service = ApplicationService(event_sink=lambda event: writer.write(event.to_dict()))
    shutting_down = False

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        request_id = "unknown"
        try:
            request = decode_request(line)
            request_id = str(request["id"])
            method = str(request["method"])
            params = request["params"]

            if method == "hello":
                result = {
                    "backend": "DubCanvas",
                    "version": __version__,
                    "protocol_version": 1,
                }
            elif method == "capabilities":
                result = service.capabilities()
            elif method == "system_check":
                result = service.system_check()
            elif method == "list_voices":
                result = service.list_voices()
            elif method == "run_job":
                result = {"job_id": service.start_job(params, analysis=False)}
            elif method == "analyze_characters":
                result = {"job_id": service.start_job(params, analysis=True)}
            elif method == "cancel_job":
                job_id = str(params.get("job_id") or "")
                if not job_id:
                    raise ValueError("job_id is required")
                result = {"cancelled": service.cancel_job(job_id)}
            elif method == "pause_job":
                job_id = str(params.get("job_id") or "")
                if not job_id:
                    raise ValueError("job_id is required")
                result = {"paused": service.pause_job(job_id)}
            elif method == "resume_dub":
                result = {"job_id": service.resume_dub(
                    str(params.get("output_dir") or ""),
                    str(params.get("project_id") or ""),
                    str(params.get("dub_id") or ""),
                    api_key=str(params.get("elevenlabs_api_key") or ""),
                )}
            elif method == "approve_review":
                result = service.approve_review(
                    str(params.get("output_dir") or ""),
                    str(params.get("project_id") or ""),
                    str(params.get("dub_id") or ""),
                    dict(params.get("revisions") or {}),
                )
            elif method == "get_job":
                job_id = str(params.get("job_id") or "")
                if not job_id:
                    raise ValueError("job_id is required")
                result = service.get_job(job_id)
            elif method == "list_jobs":
                result = service.list_jobs()
            elif method == "list_projects":
                result = service.list_projects(str(params.get("output_dir") or ""))
            elif method == "create_project":
                result = service.create_project(
                    str(params.get("output_dir") or ""), str(params.get("source") or ""),
                    str(params.get("name") or ""), str(params.get("series_id") or ""),
                    str(params.get("source_title") or ""),
                )
            elif method == "inspect_source":
                result = service.inspect_source(str(params.get("source") or ""))
            elif method == "update_project":
                result = service.update_project(
                    str(params.get("output_dir") or ""), str(params.get("project_id") or ""),
                    str(params.get("name") or ""), str(params.get("series_id") or ""),
                )
            elif method == "delete_dub":
                result = service.delete_dub(
                    str(params.get("output_dir") or ""), str(params.get("project_id") or ""),
                    str(params.get("dub_id") or ""),
                )
            elif method == "delete_project":
                result = service.delete_project(
                    str(params.get("output_dir") or ""), str(params.get("project_id") or ""),
                )
            elif method == "get_project":
                result = service.get_project(
                    str(params.get("output_dir") or ""),
                    str(params.get("project_id") or ""),
                )
            elif method == "list_character_maps":
                result = service.list_character_maps(str(params.get("output_dir") or ""))
            elif method == "get_characters":
                result = service.get_characters(str(params.get("path") or ""))
            elif method == "update_character":
                result = service.update_character(
                    str(params.get("path") or ""),
                    str(params.get("character_id") or ""),
                    dict(params.get("updates") or {}),
                )
            elif method == "preview_voice":
                result = service.preview_voice(dict(params or {}))
            elif method == "shutdown":
                result = {"shutting_down": True}
                shutting_down = True
            else:
                raise ValueError(f"Unknown method: {method}")

            writer.write(response_ok(request_id, result))
        except Exception as exc:
            writer.write(response_error(request_id, str(exc), type(exc).__name__))

        if shutting_down:
            break

    return 0


def main() -> None:
    raise SystemExit(serve())


if __name__ == "__main__":
    main()
