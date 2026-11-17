"""Resumable, selective subtitle quality review.

Suggestions are kept separate from the published subtitles: a model cannot
reliably establish what was said without listening to the source recording.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Mapping

from .core import DEFAULT_CONTEXT, DEFAULT_GLOSSARY, _atomic_json_write, _response_text, glossary_string, parse_translation_response
from .languages import source_name, target_name
from .prompts import render_prompt


TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})")
HAN = re.compile(r"[\u3400-\u9fff]")
KANA = re.compile(r"[\u3040-\u30ff]")
ODD = re.compile(r"[\u0400-\u04ff\u0600-\u06ff\u0900-\u097f\u0e00-\u0e7f]")
SCRIPTS = {
    "zh": HAN, "ja": re.compile(r"[\u3040-\u30ff\u3400-\u9fff]"),
    "ko": re.compile(r"[\uac00-\ud7af]"), "hi": re.compile(r"[\u0900-\u097f]"),
    "ar": re.compile(r"[\u0600-\u06ff]"),
}
# Kanji-only Japanese and Chinese use the same Unicode block. Script alone
# cannot tell them apart; compare source and translation before raising a flag.
LATIN = re.compile(r"[A-Za-z\u00c0-\u024f]")
LATIN_LANGUAGES = frozenset({"en", "es", "fr", "de", "pt", "it", "nl", "sv", "da", "no", "fi", "pl", "cs", "sk", "ro", "hu", "tr", "vi", "id", "ms", "tl", "sw", "hr", "sr", "sl", "et", "lv", "lt", "is", "ca", "gl", "eu", "af"})


def _source_script(language: str) -> re.Pattern | None:
    return SCRIPTS.get(language) or (LATIN if language in LATIN_LANGUAGES else None)


def _expected_source(text: str, language: str) -> bool:
    script = _source_script(language)
    return bool(script and script.search(text))


def _copied_chinese(source: str, text: str, language: str, source_language: str) -> bool:
    if source_language != "zh" or language != "ja" or KANA.search(text) or LATIN.search(text):
        return False
    source_han = "".join(HAN.findall(source))
    return len(source_han) >= 8 and source_han == "".join(HAN.findall(text))


def _wrong_target_script(text: str, language: str, source: str = "", source_language: str = "") -> bool:
    return bool((text and not SCRIPTS.get(language, LATIN).search(text)
                 and (HAN.search(text) or ODD.search(text)))
                or _copied_chinese(source, text, language, source_language))


def read_srt(path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    entries = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = block.splitlines()
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        stamps = TIME.findall(lines[timing])
        if len(stamps) != 2:
            raise ValueError(f"Invalid SRT timestamp in {path}: {lines[timing]}")
        seconds = [int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000 for h, m, s, ms in stamps]
        entries.append({"start": seconds[0], "end": seconds[1], "text": " ".join(lines[timing + 1:]).strip()})
    if not entries:
        raise ValueError(f"No subtitle cues found in {path}")
    return entries


def flags_for(source: dict, target: dict, language: str = "en", source_language: str = "zh") -> list[str]:
    original, translation = source["text"], target["text"]
    duration = target["end"] - target["start"]
    reasons = []
    if not original or not translation:
        reasons.append("empty_text")
    if duration <= 0:
        reasons.append("invalid_timing")
    elif duration < 0.55 and len(translation) > 5:
        reasons.append("very_short_slot")
    elif len(translation) / duration > 22 and len(translation) > 24:
        reasons.append("reading_speed")
    if abs(source["start"] - target["start"]) > 0.05 or abs(source["end"] - target["end"]) > 0.05:
        reasons.append("timing_mismatch")
    if source_language == "zh" and ODD.search(original):
        reasons.append("mixed_script_in_source")
    expected = _source_script(source_language)
    other_script = HAN if source_language not in {"zh", "ja"} else LATIN
    if expected and original and not expected.search(original) and other_script.search(original):
        reasons.append("non_chinese_source" if source_language == "zh" else "source_language_mismatch")
    target_script = SCRIPTS.get(language, LATIN)
    # A short unchanged kanji name is often correct Japanese. A longer line
    # whose Han characters survive unchanged (even with punctuation edits) is
    # more likely untranslated. Neither case is decidable from script alone.
    target_han = "".join(HAN.findall(translation))
    ambiguous_kanji = (source_language == "zh" and language == "ja" and bool(target_han)
                       and not KANA.search(translation) and not LATIN.search(translation))
    copied_chinese = _copied_chinese(original, translation, language, source_language)
    if (expected and translation and language != source_language and expected.search(translation)
            and (not target_script.search(translation) or copied_chinese
                 or (source_language == "zh" and language == "en"))):
        reasons.append("untranslated_chinese" if source_language == "zh" else "untranslated_source")
    if language == "en" and source_language == "zh" and re.search(r"\b(?:old six|force the king)\b", translation, re.I):
        reasons.append("literal_idiom")
    if language != source_language and original.strip() == translation.strip() and not (ambiguous_kanji and not copied_chinese):
        reasons.append("untranslated_text")
    return reasons


def approved_revisions(path: Path, report: dict, cue_count: int) -> dict[int, str] | None:
    """Return approved cue edits, or None when review still needs a decision."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("signature") != report["signature"]:
            return None
        revisions = data["revisions"]
        if not isinstance(revisions, dict):
            raise ValueError("Invalid review revisions")
        approved = {}
        for key, value in revisions.items():
            idx = int(key) - 1
            if idx < 0 or idx >= cue_count or not isinstance(value, str) or not value.strip():
                raise ValueError("Review decision contains an invalid cue or empty text")
            approved[idx] = value.strip()
        return approved
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"Invalid subtitle review approval: {exc}") from exc


def review_subtitles(
    source_path: Path,
    target_path: Path,
    report_path: Path,
    *,
    language: str = "en",
    source_language: str = "zh",
    model: str = "",
    context: str = DEFAULT_CONTEXT,
    glossary: Mapping[str, str] | None = None,
    audio: Path | None = None,
    asr_model: str = "mlx-community/whisper-large-v3-mlx",
    max_lines: int = 0,
    sample_seconds: float = 0,
    focus_cues: set[int] | None = None,
    progress: Callable[[str], None] = print,
) -> dict:
    """Flag suspect cues, optionally ask a larger local LLM for suggestions.

    Complete entries are cached one by one; a crash can resume without
    requesting the same expensive review. The original SRT is never modified.
    """
    started = time.monotonic()
    speech_name = "Mandarin" if source_language == "zh" else source_name(source_language)
    glossary = dict(DEFAULT_GLOSSARY if glossary is None else glossary)
    source, target = read_srt(source_path), read_srt(target_path)
    if len(source) != len(target):
        raise ValueError("The source and translated SRT have different cue counts; review needs aligned cues.")
    try:
        previous = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    except (ValueError, OSError):
        previous = {}
    previous_model = previous.get("review_model") if isinstance(previous, dict) else None
    signature_model = model or previous_model or ""
    audio_identity = ((str(audio.resolve()), audio.stat().st_size, audio.stat().st_mtime_ns)
                      if audio else previous.get("audio_identity") if isinstance(previous, dict) else None)
    signature_asr_model = asr_model if audio else previous.get("asr_model") if isinstance(previous, dict) else None
    signature = hashlib.sha256(json.dumps({
        "source": source, "target": target, "language": language, "model": signature_model,
        **({"source_language": source_language} if source_language != "zh" else {}),
        "context": context, "glossary": glossary,
        "audio": audio_identity,
        "asr_model": signature_asr_model,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    existing = {}
    if isinstance(previous, dict) and previous.get("signature") == signature:
        existing = {item["cue"]: item for item in previous.get("flags", []) if isinstance(item, dict) and "cue" in item}
    timing_path = report_path.with_name(report_path.name.replace(".review.json", ".timing.json"))
    try:
        timing_data = json.loads(timing_path.read_text(encoding="utf-8")) if timing_path.exists() else {}
    except (OSError, ValueError):
        timing_data = {}
    timing_issues = {int(key): value for key, value in timing_data.items()
                     if str(key).isdigit() and isinstance(value, dict)
                     and 1 <= int(key) <= len(target)}
    flagged = []
    for idx, (a, b) in enumerate(zip(source, target), start=1):
        reasons = flags_for(a, b, language, source_language)
        issue = timing_issues.get(idx)
        if issue:
            reasons.append("speech_overlap")
        if reasons:
            row = existing.get(idx, {})
            flagged.append({"cue": idx, "start": b["start"], "end": b["end"],
                            "source": a["text"],
                            "translation": issue.get("attempted_translation", b["text"]) if issue else b["text"],
                            "reasons": reasons,
                            **{key: row[key] for key in ("suggestion", "asr_candidate", "asr_translation", "review_error")
                               if row.get(key) and (not issue or row.get("translation") == issue.get("attempted_translation"))}})
    # Speed alone is common in short anime subtitle cues. Prioritize obvious
    # language errors and the worst timing cases for expensive model calls.
    selected = [row for row in flagged if (not sample_seconds or row["start"] < sample_seconds)
                and (any(reason != "reading_speed" for reason in row["reasons"])
                     or len(row["translation"]) / max(0.01, row["end"] - row["start"]) > 35)]
    if max_lines > 0:
        selected = selected[:max_lines]
    result = {"signature": signature, "source_file": str(source_path),
              "translation_file": str(target_path), "source_language": source_language,
              "target_language": language, "review_model": signature_model or None,
              "audio_identity": audio_identity, "asr_model": signature_asr_model,
              "total_cues": len(source), "flagged_cues": len(flagged),
              "sampled_cues": len(selected), "priority_cues": [row["cue"] for row in selected],
              "timing_issues": timing_issues,
              "flags": flagged,
              "timing_seconds": {}}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(report_path, result)
    if audio:
        import mlx_whisper
        asr_start = time.monotonic()
        pending_asr = [row for row in selected if (focus_cues is None or row["cue"] in focus_cues)
                       and not row.get("asr_candidate") and
                       any(reason in row["reasons"] for reason in
                           ("mixed_script_in_source", "non_chinese_source", "source_language_mismatch",
                            "untranslated_text", "untranslated_chinese", "untranslated_source"))]
        for n, row in enumerate(pending_asr, 1):
            with tempfile.TemporaryDirectory(prefix="animedubber-review-") as tmp:
                clip = Path(tmp) / "clip.wav"
                start = max(0.0, row["start"] - 0.5)
                duration = row["end"] - start + 0.5
                subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(start),
                                "-i", str(audio), "-t", str(duration), "-ac", "1", "-ar", "16000", str(clip)],
                               check=True)
                alternate = mlx_whisper.transcribe(str(clip), path_or_hf_repo=asr_model,
                                                   language=source_language, task="transcribe")
                value = str(alternate.get("text", "")).strip()
                if value:
                    row["asr_candidate"] = value
                result["timing_seconds"]["alternate_asr"] = round(time.monotonic() - asr_start, 2)
                _atomic_json_write(report_path, result)
                progress(f"Alternate transcription: {n}/{len(pending_asr)} flagged cues")
    if model:
        from mlx_lm import generate, load
        pending = [row for row in selected if (focus_cues is None or row["cue"] in focus_cues)
                   and (not row.get("suggestion") or
                        (set(row["reasons"]) & {"non_chinese_source", "source_language_mismatch"} and
                         _expected_source(row.get("asr_candidate", ""), source_language) and
                         not row.get("asr_translation")))]
        if pending:
            load_start = time.monotonic()
            progress(f"Loading selective review model: {model}")
            llm, tokenizer = load(model)
            result["timing_seconds"]["model_load"] = round(time.monotonic() - load_start, 2)
            generation_start = time.monotonic()
            glossary_text = glossary_string(glossary)
            for n, row in enumerate(pending, 1):
                if (set(row["reasons"]) & {"non_chinese_source", "source_language_mismatch"} and
                        _expected_source(row.get("asr_candidate", ""), source_language) and
                        not row.get("asr_translation")):
                    # Translate the alternate independently, without the original
                    # English guess to anchor the model to a suspect transcript.
                    alternate_prompt = (
                        f"Translate this tentative {speech_name} transcription into {target_name(language)}. "
                        "Return only the translation in one short line, with no notes. "
                        "The transcription may be wrong, so do not add context.\n"
                        f"{speech_name}: {row['asr_candidate']}"
                    )
                    alternate_prompt = render_prompt(tokenizer, alternate_prompt)
                    alternate_text = _response_text(generate(llm, tokenizer, prompt=alternate_prompt,
                                                             max_tokens=128, verbose=False)).strip()
                    alternate_text = re.sub(r"^```[^\n]*\n|\n```$", "", alternate_text).strip().strip('"')
                    if (alternate_text and len(alternate_text.splitlines()) == 1
                            and len(alternate_text) <= 200 and
                            not alternate_text.startswith(("{", "[", "<")) and
                            not _wrong_target_script(alternate_text, language, row.get("asr_candidate", ""), source_language)):
                        row["asr_translation"] = alternate_text
                        _atomic_json_write(report_path, result)
                if row.get("suggestion"):
                    continue
                idx = row["cue"] - 1
                issue = timing_issues.get(row["cue"])
                if issue and issue.get("duration") and issue.get("available"):
                    available = max(0.1, float(issue["available"]))
                    generated = max(0.1, float(issue["duration"]))
                    word_count = len(row["translation"].split())
                    target_words = max(3, math.floor(word_count * min(1.0, available / generated) * 0.8))
                    timing_instruction = (f"Generated speech took {generated:.2f}s; only {available:.2f}s "
                                          f"is free before the next voice. Aim for at most {target_words} "
                                          f"{'characters' if language in {'zh', 'ja'} else 'spoken words'}, preserve the meaning, and do not omit names or key facts.")
                else:
                    timing_instruction = f"Subtitle time window: {row['end'] - row['start']:.2f}s."
                neighbors = [{"source": source[j]["text"], "translation": target[j]["text"]}
                             for j in range(max(0, idx - 2), min(len(source), idx + 3)) if j != idx]
                source_mismatch = bool(set(row["reasons"]) & {"non_chinese_source", "source_language_mismatch"})
                source_guidance = (
                    f"The source transcript is not {speech_name} and may be a recognition error. "
                    f"If the alternate {speech_name} transcript is plausible, translate that instead; "
                    "treat it as unverified, and do not infer extra words from context. "
                    if source_mismatch else ""
                )
                prompt = (
                    f"Check this {target_name(language)} subtitle against its {source_name(source_language)} source. Suggest a short, natural translation "
                    "that fits the timing. Do not invent missing source speech. Return ONLY a JSON array "
                    f"with one object: {{\"id\": {row['cue']}, \"text\": \"suggestion\"}}.\n"
                    f"{source_guidance}"
                    f"Context: {context}\nGlossary: {glossary_text}\n"
                    f"Neighboring cues: {json.dumps(neighbors, ensure_ascii=False)}\n"
                    f"Source: {row['source']}\n"
                    f"Alternate transcription (unverified): {row.get('asr_candidate', '')}\n"
                    f"Existing translation: {row['translation']}\n"
                    f"{timing_instruction}\n"
                    f"Flagged for: {', '.join(row['reasons'])}"
                )
                rendered = render_prompt(tokenizer, prompt)
                response = generate(llm, tokenizer, prompt=rendered, max_tokens=256, verbose=False)
                parsed = parse_translation_response(_response_text(response), [row["cue"]])
                suggestion = parsed.get(row["cue"], "")
                unchanged_mismatch = (source_mismatch and _expected_source(row.get("asr_candidate", ""), source_language)
                                      and suggestion.casefold().strip() == row["translation"].casefold().strip())
                if not suggestion or unchanged_mismatch or _wrong_target_script(suggestion, language, row["source"], source_language):
                    # Smaller local models sometimes answer in prose instead of JSON.
                    # Retry this cue once with a simpler format before asking for a manual decision.
                    suggestion = ""
                    alternate = row.get("asr_candidate", "")
                    retry_prompt = (
                        f"Translate only the {speech_name} speech into natural {target_name(language)}. "
                        "Return only one short translated line; no JSON, notes, or explanation.\n"
                        f"{speech_name} transcription: {alternate if source_mismatch and _expected_source(alternate, source_language) else row['source']}\n"
                        f"Unverified existing translation: {row['translation']}\n"
                        f"Context: {context}\n{timing_instruction}"
                    )
                    retry_prompt = render_prompt(tokenizer, retry_prompt)
                    retry = _response_text(generate(llm, tokenizer, prompt=retry_prompt,
                                                   max_tokens=128, verbose=False)).strip()
                    retry = re.sub(r"^```[^\n]*\n|\n```$", "", retry).strip().strip('"')
                    if (retry and len(retry.splitlines()) == 1 and len(retry) <= 200
                            and not retry.startswith(("{", "[", "<"))
                            and not _wrong_target_script(retry, language, row["source"], source_language)
                            and not (source_mismatch and _expected_source(alternate, source_language)
                                     and retry.casefold() == row["translation"].casefold())):
                        suggestion = retry
                if suggestion:
                    row["suggestion"] = suggestion
                    row.pop("review_error", None)
                elif row.get("asr_translation") and row["asr_translation"].casefold() != row["translation"].casefold():
                    row["suggestion"] = row["asr_translation"]
                    row.pop("review_error", None)
                else:
                    row["review_error"] = (
                        "Source transcription may be wrong; check the audio and edit this line before approving."
                        if source_mismatch else
                        "The model could not suggest wording; edit this line or keep the original after checking it."
                    )
                result["timing_seconds"]["review_generation"] = round(time.monotonic() - generation_start, 2)
                _atomic_json_write(report_path, result)
                progress(f"Selective review: {n}/{len(pending)} flagged cues")
    result["timing_seconds"]["total"] = round(time.monotonic() - started, 2)
    _atomic_json_write(report_path, result)
    return result
