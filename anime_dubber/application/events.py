from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class AppEvent:
    event: str
    data: Dict[str, Any] = field(default_factory=dict)
    job_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        payload = {
            "type": "event",
            "event": self.event,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        if self.job_id:
            payload["job_id"] = self.job_id
        return payload


_STAGE_PATTERNS = [
    ("reviewing", ("Reviewing subtitles", "Loading selective review", "Subtitle review")),
    ("visual_timing", ("Visual timing", "Visual speech", "mouth motion")),
    ("downloading", ("Downloading source", "Downloading video")),
    ("extracting_audio", ("Extracting soundtrack", "Extracting audio")),
    ("separating_stems", ("Separating", "Demucs", "dialogue/vocal")),
    ("transcribing", ("Transcribing", "Whisper transcription")),
    ("translating", ("Translating", "translation LLM", "Whisper translation")),
    ("analyzing_characters", ("Analyzing characters", "speaker", "Character map")),
    ("synthesizing", ("Generating English voice", "Generating dub voice", "Synthesizing", "TTS")),
    ("mixing", ("Rendering English", "Restoring original", "Mixing English", "Mixing dub", "Checking voice clips", "Rendering dub timeline", "Preparing music and effects", "Combining dub timeline", "Combining music and effects")),
    ("exporting", ("Creating final", "mux")),
]


def progress_to_event(message: str, job_id: str) -> AppEvent:
    raw = str(message)
    if raw.startswith("__DOWNLOAD_PROGRESS__|"):
        parts = raw.split("|")
        try:
            pct = max(0.0, min(100.0, float(parts[1])))
        except Exception:
            pct = 0.0
        data: Dict[str, Any] = {
            "stage": "downloading",
            "title": "Downloading source",
            "fraction": pct / 100.0,
        }
        if len(parts) > 2 and parts[2]:
            data["speed"] = parts[2]
        if len(parts) > 3 and parts[3]:
            data["eta"] = parts[3]
        if len(parts) > 4 and parts[4]:
            data["total"] = parts[4]
        if len(parts) > 5 and parts[5]:
            data["attempt"] = parts[5]
        if len(parts) > 6 and parts[6]:
            data["attempts"] = parts[6]
        return AppEvent("progress", data, job_id)

    clean = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    last_line = next((line.strip() for line in reversed(clean.splitlines()) if line.strip()), clean)
    lower = last_line.casefold()

    if last_line.startswith("DONE:"):
        return AppEvent("stage", {"stage": "completed", "title": "Completed", "detail": last_line}, job_id)

    stage = "preparing"
    for candidate, needles in _STAGE_PATTERNS:
        if any(n.casefold() in lower for n in needles):
            stage = candidate
            break

    data: Dict[str, Any] = {"stage": stage, "title": last_line or "Working"}

    voice_match = re.search(r"Generating (?:English|dub) voice:\s*(\d+)\s*/\s*(\d+)", last_line, re.I)
    if voice_match:
        done = int(voice_match.group(1))
        total = max(1, int(voice_match.group(2)))
        data.update({"stage": "synthesizing", "completed": done, "total": total, "fraction": done / total})

    step_match = re.search(
        r"(?:Checking voice clips|Rendering dub timeline|Preparing music and effects):\s*(\d+)\s*/\s*(\d+)",
        last_line, re.I,
    )
    if step_match:
        done = int(step_match.group(1))
        total = max(1, int(step_match.group(2)))
        data.update({"stage": "mixing", "completed": done, "total": total, "fraction": done / total})

    return AppEvent("stage", data, job_id)
