from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict

from . import __version__
from .application import AppEvent, ApplicationService
from .core import DEFAULT_CONTEXT
from .languages import TARGET_LANGUAGES


def default_output_dir(base: Path | None = None) -> str:
    root = base or Path.cwd()
    current = root / "DubCanvasOutput"
    legacy = root / "AnimeDubberOutput"
    if not current.exists() and legacy.is_dir():
        return str(legacy)
    return str(current)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dubcanvas",
        description="DubCanvas command line interface",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check backend and provider availability")

    def add_job_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("source", help="YouTube URL or local video path")
        p.add_argument("-o", "--output", default=default_output_dir())
        p.add_argument("--series-id", default="")
        p.add_argument("--target-language", choices=list(TARGET_LANGUAGES), default="en")
        p.add_argument("--source-language", default="auto", help="Source language code, or auto to detect it")
        p.add_argument("--dub-name", default="", help="Name of this dub version")
        p.add_argument(
            "--asr",
            choices=["auto", "mlx-whisper", "faster-whisper"],
            default="auto",
            help="Speech recognition backend. auto uses MLX on Apple silicon, Faster-Whisper elsewhere.",
        )
        p.add_argument("--faster-whisper-model", default="large-v3")
        p.add_argument("--mlx-whisper-model", default="mlx-community/whisper-large-v3-mlx")
        p.add_argument("--faster-whisper-device", default="auto", help="auto, cpu, or cuda")
        p.add_argument("--faster-whisper-compute-type", default="auto")
        p.add_argument(
            "--translation",
            choices=["auto", "llm", "ollama", "whisper"],
            default="auto",
            help="auto uses MLX LLM when available, otherwise Whisper direct translation.",
        )
        p.add_argument("--ollama-url", default="http://127.0.0.1:11434")
        p.add_argument("--ollama-model", default="qwen3:4b")
        p.add_argument("--llm-model", default="mlx-community/Qwen3-8B-4bit")
        p.add_argument(
            "--tts",
            choices=["auto", "chatterbox", "kokoro", "macos", "piper", "elevenlabs"],
            default="auto",
            help="auto prefers Chatterbox, then Kokoro, then platform fallbacks.",
        )
        p.add_argument("--voice", default="")
        p.add_argument("--chatterbox-reference", default="", help="Optional voice reference clip you have permission to use")
        p.add_argument("--no-auto-source-voices", action="store_true", help="Do not choose source dialogue as character voice references")
        p.add_argument("--chatterbox-expressiveness", type=float, default=0.5)
        p.add_argument("--chatterbox-device", choices=["auto", "mps", "cuda", "cpu"], default="auto")
        p.add_argument("--chatterbox-standard", action="store_true", help="Use standard Chatterbox instead of Turbo")
        p.add_argument("--preserve-source-accent", action="store_true",
                       help="Disable accent-transfer mitigation when cloning a non-English voice into English")
        p.add_argument("--kokoro-voice", default="auto", help="Kokoro voice preset, e.g. af_heart or am_adam")
        p.add_argument("--piper-model", default="", help="Path to a Piper .onnx voice model")
        p.add_argument("--piper-speaker", type=int, default=-1, help="Optional Piper speaker id for multi-speaker models")
        p.add_argument("--rate", type=int, default=210)
        p.add_argument("--speaker-backend", choices=["auto", "ecapa", "acoustic"], default="auto")
        p.add_argument("--max-speakers", type=int, default=12)
        p.add_argument("--speaker-threshold", type=float, default=0.0)
        p.add_argument("--no-characters", action="store_true")
        p.add_argument("--no-resume", action="store_true")
        p.add_argument("--no-auto-review", action="store_false", dest="review_before_dub",
                       help="Skip automatic subtitle quality review")
        p.add_argument("--review-before-dub", action="store_true", dest="review_before_dub",
                       help="Run automatic subtitle quality review (the default)")
        p.add_argument("--review-model", default="mlx-community/Qwen3-8B-4bit")
        p.add_argument("--force", action="store_true")
        p.add_argument("--ducking", action="store_true")
        p.add_argument("--background-volume", type=float, default=1.0)
        p.add_argument("--dub-volume", type=float, default=1.15)
        p.add_argument("--elevenlabs-api-key", default="")
        p.add_argument("--json", action="store_true", help="Print machine-readable final result")

    run = sub.add_parser("run", help="Generate subtitles or a dub version")
    add_job_args(run)
    run.add_argument("--subtitles-only", action="store_true")

    analyze = sub.add_parser("analyze", help="Analyze characters and write the character map")
    add_job_args(analyze)

    caps = sub.add_parser("capabilities", help="Show detected platform/provider capabilities")
    caps.add_argument("--json", action="store_true")

    projects = sub.add_parser("projects", help="List source projects and their dub versions")
    projects.add_argument("-o", "--output", default=default_output_dir())
    create = sub.add_parser("new-project", help="Register a source video before processing")
    create.add_argument("source")
    create.add_argument("-o", "--output", default=default_output_dir())
    create.add_argument("--name", required=True, help="Unique project name")
    create.add_argument("--series-id", default="")
    retry = sub.add_parser("resume-dub", help="Resume a paused or failed dub in place")
    retry.add_argument("project_id")
    retry.add_argument("dub_id")
    retry.add_argument("-o", "--output", default=default_output_dir())
    retry.add_argument("--elevenlabs-api-key", default="")
    retry.add_argument("--json", action="store_true")

    approve = sub.add_parser("approve-review", help="Approve a paused dub's subtitle review and optionally apply cue edits")
    approve.add_argument("project_id")
    approve.add_argument("dub_id")
    approve.add_argument("-o", "--output", default=default_output_dir())
    approve.add_argument("--revisions", type=Path, help="Optional JSON object mapping 1-based cue numbers to approved text")

    review = sub.add_parser("review-subtitles", help="Flag suspicious cues in an existing SRT pair and optionally propose fixes with a larger local model")
    review.add_argument("source_srt", type=Path, help="Original-language SRT")
    review.add_argument("translated_srt", type=Path, help="Translated SRT with matching cue numbers")
    review.add_argument("-o", "--report", type=Path, default=None, help="JSON report path (defaults to translated SRT with .review.json suffix)")
    review.add_argument("--target-language", choices=list(TARGET_LANGUAGES), default="en")
    review.add_argument("--source-language", default="zh", help="Source language code (default: zh for existing SRT pairs)")
    review.add_argument("--context", default=None, help="Project context for model review")
    review.add_argument("--glossary-json", default=None, help="JSON object with project translation terms")
    review.add_argument("--model", default="", help="Optional MLX model for flagged cues, e.g. mlx-community/Qwen3-8B-4bit")
    review.add_argument("--audio", type=Path, help="Optional extracted dialogue WAV for a second transcription of suspect source cues")
    review.add_argument("--asr-model", default="mlx-community/whisper-large-v3-mlx")
    review.add_argument("--sample-seconds", type=float, default=0, help="Limit expensive checks to flagged cues beginning in the first N seconds (300 = five minutes)")
    review.add_argument("--max-lines", type=int, default=0, help="Review up to this many flagged cues to benchmark before a full review")
    review.add_argument("--cue", type=int, action="append", help="Run the stronger checks only on this 1-based cue; repeat for more cues")

    return parser


def _payload(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "source": args.source,
        "output_dir": str(Path(args.output).expanduser()),
        "series_id": args.series_id,
        "target_language": args.target_language,
        "source_language": args.source_language,
        "dub_name": args.dub_name,
        "mode": "subtitles" if getattr(args, "subtitles_only", False) else "dub",
        "asr": {
            "provider": args.asr,
            "mlx_model": args.mlx_whisper_model,
            "model": args.faster_whisper_model,
            "device": args.faster_whisper_device,
            "compute_type": args.faster_whisper_compute_type,
        },
        "translation": {
            "provider": args.translation,
            "llm_model": args.llm_model,
            "ollama_url": args.ollama_url,
            "model": args.ollama_model,
        },
        "tts": {
            "provider": args.tts,
            "fallback_voice": args.voice,
            "chatterbox_reference_audio": args.chatterbox_reference,
            "auto_voice_references": not args.no_auto_source_voices,
            "chatterbox_expressiveness": args.chatterbox_expressiveness,
            "chatterbox_device": args.chatterbox_device,
            "chatterbox_turbo": not args.chatterbox_standard,
            "prefer_american_accent": not args.preserve_source_accent,
            "kokoro_voice": args.kokoro_voice,
            "piper_model": args.piper_model,
            "piper_speaker": args.piper_speaker,
            "rate": args.rate,
            "api_key": args.elevenlabs_api_key,
        },
        "speaker_backend": args.speaker_backend,
        "max_speakers": args.max_speakers,
        "speaker_threshold": args.speaker_threshold,
        "multi_character": not args.no_characters,
        "resume": not args.no_resume,
        "review_before_dub": args.review_before_dub,
        "review_model": args.review_model,
        "force": args.force,
        "ducking": args.ducking,
        "background_volume": args.background_volume,
        "dub_volume": args.dub_volume,
        "elevenlabs_api_key": args.elevenlabs_api_key,
        "chatterbox_reference_audio": args.chatterbox_reference,
        "auto_voice_references": not args.no_auto_source_voices,
        "chatterbox_expressiveness": args.chatterbox_expressiveness,
        "chatterbox_device": args.chatterbox_device,
        "chatterbox_turbo": not args.chatterbox_standard,
        "prefer_american_accent": not args.preserve_source_accent,
        "kokoro_voice": args.kokoro_voice,
        "piper_model": args.piper_model,
        "piper_speaker": args.piper_speaker,
    }


def _print_event(event: AppEvent) -> None:
    if event.event == "progress":
        fraction = event.data.get("fraction")
        title = event.data.get("title", "Working")
        if isinstance(fraction, (int, float)):
            print(f"\r{title}: {fraction * 100:5.1f}%", end="", flush=True)
        return

    if event.event == "log":
        message = str(event.data.get("message", ""))
        if message.startswith("__DOWNLOAD_PROGRESS__|"):
            return
        print(message)
    elif event.event == "error":
        print(f"ERROR: {event.data.get('message', 'Unknown error')}", file=sys.stderr)


def _doctor(service: ApplicationService) -> int:
    report = service.system_check()
    caps = report["capabilities"]
    p = caps["platform"]
    print(f"Platform: {p['system']} {p['machine']} · Python {p['python']}")
    for check in report["checks"]:
        mark = "✓" if check["ok"] else "✗"
        print(f"{mark} {check['name']}: {check['detail']}")
    note = caps["current_pipeline"]["note"]
    print(f"\nCurrent engine: {caps['current_pipeline']['engine']}")
    print(note)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = ApplicationService(event_sink=_print_event)

    if args.command == "doctor":
        return _doctor(service)

    if args.command == "capabilities":
        caps = service.capabilities()
        if args.json:
            print(json.dumps(caps, indent=2))
        else:
            print(json.dumps(caps, indent=2))
        return 0

    if args.command == "projects":
        print(json.dumps(service.list_projects(args.output), indent=2))
        return 0
    if args.command == "new-project":
        print(json.dumps(service.create_project(args.output, args.source, args.name, args.series_id), indent=2))
        return 0
    if args.command == "review-subtitles":
        from .review import review_subtitles
        try:
            report_path = args.report or args.translated_srt.with_suffix(".review.json")
            report = review_subtitles(args.source_srt, args.translated_srt, report_path,
                                      language=args.target_language, source_language=args.source_language, model=args.model,
                                      context=args.context or DEFAULT_CONTEXT,
                                      glossary=json.loads(args.glossary_json) if args.glossary_json else None,
                                      audio=args.audio, asr_model=args.asr_model,
                                      max_lines=args.max_lines, sample_seconds=args.sample_seconds,
                                      focus_cues=set(args.cue) if args.cue else None,
                                      progress=lambda msg: print(msg, file=sys.stderr))
            print(json.dumps({"report": str(report_path), "total_cues": report["total_cues"],
                              "flagged_cues": report["flagged_cues"],
                              "sampled_cues": report["sampled_cues"],
                              "timing_seconds": report["timing_seconds"]}, indent=2))
            return 0
        except KeyboardInterrupt:
            print("Interrupted. Completed review suggestions are saved.", file=sys.stderr)
            return 130
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.command == "approve-review":
        try:
            revisions = json.loads(args.revisions.read_text(encoding="utf-8")) if args.revisions else {}
            print(json.dumps(service.approve_review(args.output, args.project_id, args.dub_id, revisions), indent=2))
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    if args.command == "resume-dub":
        try:
            job_id = service.resume_dub(args.output, args.project_id, args.dub_id,
                                        api_key=args.elevenlabs_api_key)
            while service.get_job(job_id)["status"] in {"queued", "running"}:
                try:
                    time.sleep(.2)
                except KeyboardInterrupt:
                    service.pause_job(job_id)
                    print("\nPausing; waiting for the current operation to finish…", file=sys.stderr)
            job = service.get_job(job_id)
            if args.json:
                print(json.dumps(job, indent=2))
            elif job["status"] == "completed":
                print("Completed:", job["result"])
            else:
                print(f"{job['status'].capitalize()}: {job['error']}", file=sys.stderr)
            return 0 if job["status"] == "completed" else 1
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    analysis = args.command == "analyze"
    try:
        job = service.run_sync(_payload(args), analysis=analysis)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(job, indent=2))
    else:
        print()
        print("Completed." if job["status"] == "completed" else f"{job['status'].capitalize()}: {job.get('error') or 'Review required'}")
        for name, path in job.get("result", {}).items():
            print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
