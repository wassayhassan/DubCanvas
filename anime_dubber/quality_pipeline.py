"""Quality profile connected to DubCanvas's project/job/artifact workflow."""
from __future__ import annotations
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import re
import shutil

from .quality_models import options, setup_issues, file_digest, digest
from .quality_runtime import Worker, Checkpoints, runtime_fingerprints
from .quality_timing import edit_distance, window, choose_tempo, speech_error, validate_schedule, protected_speech_matches, chinese_critical_matches, normalize_chinese_numbers, validate_source_alignment
from .localization import Glossary, localize, review_prompt, parse_object, validate_review, scene_context, cloud_review

def _publish(runner, kind, path, language):
    callback = getattr(runner, "artifact", None)
    if callback:
        callback(kind, Path(path), language)

def _stage(runner, stage, title):
    runner.check_cancel()
    runner.set_stage(stage, title)

def _cut_audio(source, output, start, duration, runner, *, mono=True):
    from .core import _atomic_media_run
    _atomic_media_run(runner, ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(max(0, start)), "-t", str(duration), "-i", str(source), "-vn",
        "-ac", "1" if mono else "2", "-ar", "16000" if mono else "48000",
        "-c:a", "pcm_f32le", str(output)], output)

def _cached_call(worker, checkpoints, stage, inputs, request):
    cached = checkpoints.get(stage, inputs)
    if cached is not None:
        return cached
    result = worker.call(request)
    checkpoints.put(stage, inputs, result)
    return result

def separation(audio, work, q, cfg, checkpoints, runner, progress):
    from .core import ffprobe_duration, _complete_wav, _atomic_media_run, _ffconcat_quote, PipelineError
    _stage(runner, "separating_stems", "Separating dialogue, music and effects")
    identity = {key: q[key] for key in ("bandit_sha256", "bandit_config_sha256", "separator_device",
        "separation_chunk_seconds", "separation_context_seconds")}
    identity["runtime_versions"] = checkpoints.environment.get("separator_python", {})
    folder = work / ("bandit_" + digest(identity)[:16])
    folder.mkdir(parents=True, exist_ok=True)
    output = {name: folder / f"{name}.wav" for name in ("dialogue", "music", "effects")}
    duration = ffprobe_duration(audio, runner)
    if cfg.resume and not cfg.force and all(_complete_wav(p, minimum_seconds=duration * .999) for p in output.values()):
        return output
    chunks = {name: [] for name in output}
    size = q["separation_chunk_seconds"]
    with Worker("separator", q, runner) as worker:
        for i in range(math.ceil(duration / size)):
            runner.check_cancel()
            start = i * size
            length = min(size, duration - start)
            context_start = max(0, start - q["separation_context_seconds"])
            context_end = min(duration, start + length + q["separation_context_seconds"])
            chunk = folder / f"chunk_{i:05}"
            inp, out = chunk / "input", chunk / "output"
            inp.mkdir(parents=True, exist_ok=True)
            paths = {name: chunk / f"{name}.wav" for name in output}
            if cfg.force or not cfg.resume or not all(_complete_wav(p, minimum_seconds=length * .999) for p in paths.values()):
                _cut_audio(audio, inp / "audio.wav", context_start, context_end - context_start, runner, mono=False)
                worker.call({"input": str(inp), "output": str(out)})
                aliases = {"dialogue": ("speech", "dialogue", "vocals"), "music": ("music",), "effects": ("sfx", "effects", "effect")}
                for name, choices in aliases.items():
                    stem = next((p for p in out.rglob("*.wav") if p.stem.lower() in choices), None)
                    if stem is None:
                        raise PipelineError(f"BandIt omitted {name}; checkpoint/config may not match DnR-v3")
                    if ffprobe_duration(stem, runner) < context_end - context_start - .05:
                        raise PipelineError(f"BandIt {name} chunk is incomplete")
                    _cut_audio(stem, paths[name], start - context_start, length, runner, mono=False)
                checkpoints.put("separation_chunk", {**identity, "chunk": i}, {name: str(p) for name, p in paths.items()})
                shutil.rmtree(inp)
                shutil.rmtree(out)
            for name, path in paths.items():
                chunks[name].append(path)
            progress(f"Separating stems: {i + 1}/{math.ceil(duration / size)} chunks")
    for name, paths in chunks.items():
        concat = folder / f"{name}.ffconcat"
        concat.write_text("".join(f"file {_ffconcat_quote(p)}\n" for p in paths))
        _atomic_media_run(runner, ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", str(concat), "-c:a", "pcm_f32le", str(output[name])], output[name])
        if not _complete_wav(output[name], minimum_seconds=duration * .999):
            raise PipelineError(f"BandIt {name} is incomplete; mix blocked")
    return output

def scene_cuts(video, q, checkpoints, runner):
    inputs = {"threshold": .35, "configured": q["scene_cuts"]}
    cached = checkpoints.get("scene_cuts", inputs)
    if cached is not None:
        return cached
    cp = runner.run(["ffmpeg", "-hide_banner", "-i", str(video), "-an", "-vf",
        "select='gt(scene,0.35)',showinfo", "-f", "null", "-"])
    cuts = sorted(set(q["scene_cuts"] + [float(x) for x in re.findall(r"pts_time:([0-9.]+)", cp.stderr or "")]))
    checkpoints.put("scene_cuts", inputs, cuts)
    return cuts

def recognize(audio, dialogue, q, cfg, work, checkpoints, runner, progress):
    from .core import ffprobe_duration, PipelineError, Segment, _atomic_json_write
    duration = ffprobe_duration(audio, runner)
    size = q["asr_chunk_seconds"]
    folder = work / "asr_views"
    folder.mkdir(parents=True, exist_ok=True)
    windows = []
    for i in range(math.ceil(duration / size)):
        start, end = i * size, min(duration, (i + 1) * size)
        a, b = max(0, start - 1), min(duration, end + 1)
        paths = {}
        for name, source in (("original", audio), ("dialogue", dialogue)):
            path = folder / f"{i:05}_{name}.wav"
            _cut_audio(source, path, a, b - a, runner)
            paths[name] = str(path)
        windows.append({"index": i, "start": start, "end": end, "audio_start": a, "audio_end": b, "paths": paths})
    for role in ("asr", "verifier"):
        _stage(runner, "transcribing", "Recognizing Chinese" if role == "asr" else "Independently checking speech")
        with Worker(role, q, runner) as worker:
            for row in windows:
                row[role] = {}
                for view in ("original", "dialogue"):
                    inputs = {"window": [row["audio_start"], row["end"]], "view": view,
                              "model": q["models"][role], "glossary": cfg.glossary,
                              "audio_sha256": file_digest(row["paths"][view])}
                    result = _cached_call(worker, checkpoints, f"{role}_{view}", inputs,
                        {"audio": row["paths"][view], "language": "Chinese", "hotwords": list(cfg.glossary)})
                    row[role][view] = str(result.get("text", "")).strip()
                progress(f"Transcription {role}: {row['index'] + 1}/{len(windows)} chunks")
    normalize = lambda s: re.sub(r"[\W_]", "", normalize_chinese_numbers(s)).casefold()
    failures = []
    disputed = []
    for row in windows:
        hypotheses = [row[role][view] for role in ("asr", "verifier") for view in ("original", "dialogue")]
        row["hypotheses"] = hypotheses
        nonempty = [h for h in hypotheses if h]
        if not nonempty:
            row["text"] = ""
            continue
        # Require agreement between DIFFERENT recognizers, not two views of one model.
        pairs = []
        for a in hypotheses[:2]:
            for b in hypotheses[2:]:
                if a and b:
                    na, nb = normalize(a), normalize(b)
                    error = edit_distance(na, nb) / max(1, len(na), len(nb))
                    if chinese_critical_matches(a, b, cfg.glossary):
                        pairs.append((error, a, b))
        if not pairs or min(pairs)[0] > .18:
            disputed.append(row)
        row["text"] = min(pairs)[1] if pairs else nonempty[0]
    if disputed:
        with Worker("fallback", q, runner) as worker:
            for row in disputed:
                result = _cached_call(worker, checkpoints, "whisper_fallback", {"model": q["models"]["fallback"],
                    "window": row["index"], "audio_sha256": file_digest(row["paths"]["original"])},
                    {"audio": row["paths"]["original"]})
                alternative = str(result.get("text", "")).strip()
                row["hypotheses"].append(alternative)
                matches = []
                for hypothesis in row["hypotheses"][:-1]:
                    if hypothesis and alternative and chinese_critical_matches(hypothesis, alternative, cfg.glossary):
                        error = edit_distance(normalize(alternative), normalize(hypothesis)) / max(1, len(normalize(alternative)), len(normalize(hypothesis)))
                        matches.append((error, hypothesis))
                if matches and min(matches)[0] <= .18:
                    row["text"] = min(matches)[1]
                else:
                    failures.append(row["index"] + 1)
    _atomic_json_write(work / "asr-hypotheses.json", {"complete": not failures, "windows": windows,
                                                       "unresolved_windows": failures})
    provisional = [Segment(r["start"], r["end"], r["text"]) for r in windows if r["text"]]
    from .core import write_srt, write_vtt
    write_srt(provisional, work / "source-provisional.srt")
    write_vtt(provisional, work / "source-provisional.vtt")
    _publish(runner, "source_provisional_srt", work / "source-provisional.srt", "zh")
    if failures:
        raise PipelineError("Independent ASR disagrees at audio chunks " + ", ".join(map(str, failures))
                            + ". All hypotheses/provisional subtitles saved; no dub generated")
    if not provisional:
        raise PipelineError("Both recognizers found no dialogue; source audio and logs saved")
    recognized_text = "".join(s.text for s in provisional)
    han_count = len(re.findall(r"[\u3400-\u9fff]", recognized_text))
    if han_count / max(1, len(normalize(recognized_text))) < .2:
        raise PipelineError("Recognition is mostly outside Chinese. Provisional subtitles saved; choose the existing multilingual profile or verify the source language")
    _stage(runner, "aligning", "Aligning accepted Chinese dialogue")
    aligned = []
    with Worker("aligner", q, runner) as worker:
        for row in windows:
            if not row["text"]:
                continue
            problems = []
            for view in ("original", "dialogue"):
                result = _cached_call(worker, checkpoints, "source_alignment", {"model": q["models"]["aligner"],
                    "source": row["text"], "window": row["index"], "view": view, "coverage_validator": 2,
                    "audio_sha256": file_digest(row["paths"][view])},
                    {"audio": row["paths"][view], "text": row["text"], "language": "Chinese"})
                items = result.get("items", [])
                try:
                    validate_source_alignment(row["text"], items, row["audio_end"] - row["audio_start"])
                    break
                except (ValueError, TypeError, KeyError) as exc:
                    problems.append(str(exc))
                    progress(f"Automatically retrying source alignment on separated dialogue at chunk {row['index'] + 1}")
            else:
                raise PipelineError(f"Chinese alignment could not preserve complete text at chunk {row['index'] + 1}: "
                                    + "; ".join(problems) + ". Complete provisional subtitles saved; no dialogue discarded")
            for item in items:
                a, b = float(item["start_time"]) + row["audio_start"], float(item["end_time"]) + row["audio_start"]
                if not math.isfinite(a) or not math.isfinite(b) or b <= a:
                    raise PipelineError(f"Invalid Chinese word alignment in chunk {row['index'] + 1}; provisional transcript saved, no dialogue discarded")
                if row["start"] <= (a + b) / 2 < row["end"]:
                    aligned.append({"start": a, "end": b, "text": item["text"]})
    return aligned

def assign_speakers(words, tracks, cuts):
    from .core import Segment, PipelineError
    result = []
    for word in sorted(words, key=lambda x: x["start"]):
        scores = {}
        for track in tracks:
            overlap = max(0, min(word["end"], track["end"]) - max(word["start"], track["start"]))
            scores[track["speaker"]] = scores.get(track["speaker"], 0) + overlap
        label = max(scores, key=scores.get) if scores and max(scores.values()) > 0 else "UNASSIGNED"
        if label == "UNASSIGNED":
            raise PipelineError(f"Speaker unidentified at {word['start']:.2f}s; preserve ASR and retry diarization")
        if len([v for v in scores.values() if v > (word["end"] - word["start"]) * .35]) > 1:
            raise PipelineError(f"Overlapping speakers need separate acoustic transcripts at {word['start']:.2f}s; no voice invented")
        sid = "character_" + digest(label)[:8]
        previous = result[-1] if result else None
        if (previous and previous.speaker_id == sid and word["start"] - previous.end < .35
                and word["end"] - previous.start <= 8 and not re.search(r"[。！？!?]$", previous.text)
                and not any(previous.end <= c <= word["start"] for c in cuts)):
            previous.text += word["text"]
            previous.end = max(previous.end, word["end"])
        else:
            result.append(Segment(word["start"], word["end"], word["text"], speaker_id=sid))
    return result

def source_analysis(video, audio, stems, config, q, work, checkpoints, runner, progress):
    from .core import ffprobe_duration
    cuts = scene_cuts(video, q, checkpoints, runner)
    words = recognize(audio, stems["dialogue"], q, config, work, checkpoints, runner, progress)
    _stage(runner, "analyzing_characters", "Detecting overlapping speaker tracks")
    diarizer_audio = work / "diarization-16k.wav"
    _cut_audio(stems["dialogue"], diarizer_audio, 0, ffprobe_duration(audio, runner), runner)
    with Worker("diarizer", q, runner) as worker:
        tracks = _cached_call(worker, checkpoints, "diarization", {"model": q["models"]["diarizer"], "audio_sha256": file_digest(stems["dialogue"])},
                              {"audio": str(diarizer_audio)})["tracks"]
    segments = assign_speakers(words, tracks, cuts)
    import numpy as np
    import soundfile as sf
    from .characters import _acoustic_features, _load_segment, _style_for_segment
    mono = work / "style-analysis.wav"
    _cut_audio(stems["dialogue"], mono, 0, ffprobe_duration(audio, runner), runner)
    with sf.SoundFile(mono) as handle:
        features = [_acoustic_features(_load_segment(handle, s.start, s.end)) for s in segments]
    for sid in {s.speaker_id for s in segments}:
        ids = [i for i, s in enumerate(segments) if s.speaker_id == sid]
        f0s = [features[i].f0_median for i in ids if features[i].f0_median > 0]
        baseline = {"rms": float(np.median([features[i].rms_db for i in ids])),
                    "f0": float(np.median(f0s)) if f0s else 0, "flat": float(np.median([features[i].flatness for i in ids]))}
        for i in ids:
            segments[i].style, segments[i].style_confidence = _style_for_segment(features[i], baseline, segments[i].text)
    from .core import _atomic_json_write
    _atomic_json_write(work / "source-analysis.json", {"words": words, "tracks": tracks,
        "segments": [s.to_dict() for s in segments], "cuts": cuts, "source_sha256": checkpoints.identity})
    return segments, cuts

def cast(segments, config, q, path, runner):
    from .characters import CharacterProfile, write_character_map
    from .providers.tts import automatic_kokoro_voice
    prior = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    saved = {row["id"]: row for row in prior.get("characters", [])}
    profiles = {}
    shared_profiles = []
    for i, sid in enumerate(dict.fromkeys(s.speaker_id for s in segments)):
        profile = CharacterProfile.from_dict(saved[sid]) if sid in saved else CharacterProfile(sid, f"Speaker {i + 1}")
        profile.line_count = sum(s.speaker_id == sid for s in segments)
        profile.speaking_seconds = sum(s.end - s.start for s in segments if s.speaker_id == sid)
        profile.speaking_share = profile.speaking_seconds / max(.001, sum(s.end - s.start for s in segments))
        profile.embedding_backend = "pyannote-community-1"
        row = profile.to_dict()
        # Stable ID/cast persists across versions; gender is not inferred from pitch.
        default_speaker = q["qwen_speaker"] if q["qwen_speaker"] != "Aiden" else ("Aiden" if i % 2 == 0 else "Ryan")
        row["qwen_speaker"] = row.get("qwen_speaker") or saved.get(sid, {}).get("qwen_speaker") or default_speaker
        shared_profiles.append(dict(row))
        # Dub-version overrides never mutate the project's shared character choices.
        override = config.voice_overrides.get(sid, {})
        inherited = row.get("tts_provider") in {"inherit", "auto", "", None}
        explicit_qwen = (not inherited and row.get("tts_provider") == "qwen") or override.get("tts_provider") == "qwen" or bool(override.get("qwen_speaker"))
        row.update(override)
        if row.get("tts_provider") in {"inherit", "auto", "", None}:
            row["tts_provider"] = q["voice_engine"]
        if q["american_female_fallback"] and row.get("voice_class") == "female" and row["tts_provider"] == "qwen" and not explicit_qwen:
            row["tts_provider"] = "kokoro"
        if row.get("kokoro_voice") in {"", "auto", None}:
            row["kokoro_voice"] = automatic_kokoro_voice(row)
        profiles[sid] = row
        if row.get("voice_class") == "neutral" and not row.get("manual") and row["tts_provider"] in {"qwen", "kokoro"}:
            preset = row["qwen_speaker"] if row["tts_provider"] == "qwen" else row["kokoro_voice"]
            runner.progress(f"Warning: {profile.display_name} has no validated character voice-class evidence; "
                            f"using fixed preset {preset}. "
                            "Its voice stays consistent; verify casting in Speakers.")
    write_character_map({"version": 2, "series_id": config.series_id, "speaker_backend": "pyannote-community-1",
        "source_key": path.name.removesuffix("_quality_characters.json"), "pipeline_profile": "quality_v1",
        "characters": shared_profiles, "segments": [s.to_dict() for s in segments],
        "notes": {"identity": "Acoustic speaker IDs; semantic character/age/sex identity requires evidence, not pitch"}}, path)
    _publish(runner, "character_map", path, config.target_language)
    return profiles

def synthesize_takes(segments, profiles, glossary, config, q, work, checkpoints, runner, progress, *, attempts=None, delivery_hints=None):
    from .core import _complete_wav, PipelineError, CancelledError, synthesize_elevenlabs
    directory = work / "dry_voices"
    directory.mkdir(parents=True, exist_ok=True)
    takes = [None] * len(segments)
    groups = {}
    for i, seg in enumerate(segments):
        row = profiles[seg.speaker_id]
        engine = row["tts_provider"]
        if engine not in {"qwen", "kokoro", "elevenlabs", "index_clone"}:
            raise PipelineError(f"Quality profile cannot use cast engine {engine}; choose an explicit supported voice")
        groups.setdefault(engine, []).append(i)
    for engine, ids in groups.items():
        role = {"qwen": "tts", "kokoro": "kokoro", "index_clone": "index_clone"}.get(engine)
        manager = Worker(role, q, runner) if role else nullcontext(None)
        with manager as worker:
            _stage(runner, "synthesizing", "Generating expressive character dialogue")
            for i in ids:
                runner.check_cancel()
                seg, row = segments[i], profiles[segments[i].speaker_id]
                voice_text = glossary.voice_text(seg.translated)
                instruct = {"shouting": "Shout with strong emotion, preserve clear words, at natural speed.",
                            "whispering": "Whisper softly and intimately, clearly, at natural speed."}.get(seg.style,
                            "Natural expressive American English dialogue at normal speed. " + str(row.get("notes", "")))
                generation_attempt = (attempts or {}).get(i, 0)
                if (delivery_hints or {}).get(i):
                    instruct += " " + delivery_hints[i]
                if generation_attempt:
                    instruct += " Pronounce every supplied word clearly. Do not omit, repeat or add words."
                identity = {"text": voice_text, "cast": row, "style": instruct,
                    "model": q["models"]["tts"] if engine == "qwen" else engine,
                    "glossary": glossary.revision, "runtime_versions": checkpoints.environment,
                    "cloud_model": config.elevenlabs_model_id if engine == "elevenlabs" else None}
                if generation_attempt:
                    identity["generation_attempt"] = generation_attempt
                if engine == "index_clone":
                    identity["clone_config_sha256"] = file_digest(q["clone_config"])
                    identity["clone_model_directory"] = q["clone_model_dir"]
                    reference = row.get("reference_audio", "")
                    if not reference or not Path(reference).is_file():
                        raise PipelineError(f"Choose a readable reference for cloned character {seg.speaker_id}")
                    identity["reference_sha256"] = file_digest(reference)
                path = directory / f"{i:06}_{digest(identity)[:16]}.wav"
                if config.force or not config.resume or not _complete_wav(path):
                    temp = path.with_suffix(".rendering.wav")
                    try:
                        if worker:
                            try:
                                worker.call({"text": voice_text, "speaker": row.get("qwen_speaker", q["qwen_speaker"]),
                                    "voice": row["kokoro_voice"], "instruct": instruct,
                                    "reference": row.get("reference_audio", ""), "output": str(temp)})
                            except CancelledError:
                                raise
                            except PipelineError as exc:
                                if any(word in str(exc).lower() for word in ("deadline", "budget", "memory", "configure isolated")):
                                    raise
                                raise SpeechValidationFailure({i: f"voice generation failed: {exc}"}) from exc
                        else:
                            if not q["allow_cloud"]:
                                raise PipelineError("Cloud speech upload is disabled")
                            import dataclasses
                            cloud_cfg = dataclasses.replace(config, elevenlabs_voice_id=row.get("elevenlabs_voice_id") or config.elevenlabs_voice_id,
                                                            elevenlabs_model_id="eleven_v4")
                            mp3 = temp.with_suffix(".mp3")
                            cloud_text = ({"shouting": "[shouting] ", "whispering": "[whispering] "}.get(seg.style, "") + voice_text)
                            synthesize_elevenlabs(cloud_text, mp3, cloud_cfg, runner)
                            _cut_audio(mp3, temp, 0, 3600, runner, mono=False)
                            mp3.unlink(missing_ok=True)
                        if not _complete_wav(temp):
                            raise SpeechValidationFailure({i: "voice engine returned no valid audio"})
                        temp.replace(path)
                    finally:
                        temp.unlink(missing_ok=True)
                checkpoints.put("dry_speech", identity, {"path": str(path), "native_acceleration": 1.0})
                takes[i] = {"path": path, "text": voice_text, "identity": identity}
                progress(f"Generating dub voice: {i + 1}/{len(segments)} lines")
    return takes

from .core import PipelineError

class SpeechValidationFailure(PipelineError):
    def __init__(self, failures):
        self.failures = failures
        super().__init__("Speech checks failed: " + "; ".join(f"cue {i + 1}: {reason}" for i, reason in failures.items()))

def generate_checked_speech(segments, profiles, glossary, config, q, work, checkpoints, runner, progress, *, delivery_hints=None):
    """Repair only failed cues, with fixed character voices and a durable retry budget."""
    identity = {"segments": [s.to_dict() for s in segments], "cast": {sid: dict(row) for sid, row in profiles.items()},
                "glossary": glossary.revision, "settings": q, "delivery_hints": delivery_hints or {}}
    saved = checkpoints.get("speech_recovery", identity) or {}
    attempts = {int(i): count for i, count in saved.get("attempts", {}).items()}
    fallback_cast = saved.get("fallback_cast", {})
    profiles.update(fallback_cast)
    while True:
        try:
            takes = synthesize_takes(segments, profiles, glossary, config, q, work, checkpoints, runner, progress,
                                    attempts=attempts, delivery_hints=delivery_hints)
            speech_qa(takes, q, checkpoints, runner, progress)
            checkpoints.put("speech_recovery", identity, {"attempts": attempts, "validated": True, "fallback_cast": fallback_cast})
            return takes
        except SpeechValidationFailure as exc:
            exhausted = [i for i in exc.failures if attempts.get(i, 0) >= 2]
            if exhausted:
                switch = {segments[i].speaker_id for i in exhausted
                          if profiles.get(segments[i].speaker_id, {}).get("tts_provider") == "qwen"}
                if switch:
                    # Switch the entire character in this dub, never individual
                    # lines or the shared project cast, to preserve consistency.
                    for sid in switch:
                        fallback_cast[sid] = {**profiles[sid], "tts_provider": "kokoro", "automatic_fallback_from": "qwen"}
                        profiles[sid] = fallback_cast[sid]
                    for i, seg in enumerate(segments):
                        if seg.speaker_id in switch:
                            attempts[i] = 0
                    progress("Automatically using a consistent Kokoro preset for characters whose expressive voice failed word checks")
                    checkpoints.put("speech_recovery", identity, {"attempts": attempts, "validated": False, "fallback_cast": fallback_cast})
                    continue
                checkpoints.put("speech_recovery", identity, {"attempts": attempts, "validated": False, "failures": exc.failures, "fallback_cast": fallback_cast})
                raise PipelineError("Available voice engines could not produce valid speech at cues " + ", ".join(str(i + 1) for i in exhausted)
                                    + "; subtitles and generated takes preserved") from exc
            for i in exc.failures:
                attempts[i] = attempts.get(i, 0) + 1
                progress(f"Automatically regenerating cue {i + 1}, take {attempts[i] + 1}/3: {exc.failures[i]}")
            checkpoints.put("speech_recovery", identity, {"attempts": attempts, "validated": False, "failures": exc.failures, "fallback_cast": fallback_cast})

def speech_qa(takes, q, checkpoints, runner, progress):
    from .core import PipelineError
    _stage(runner, "speech_quality", "Checking generated words and pronunciation")
    failures = {}
    with Worker("verifier", q, runner) as worker:
        for i, take in enumerate(takes):
            result = _cached_call(worker, checkpoints, "speech_content", {"take": take["identity"],
                "audio": file_digest(take["path"]), "model": q["models"]["verifier"]},
                {"audio": str(take["path"]), "language": "English"})
            error = speech_error(take["text"], result.get("text", ""))
            if error > q["qa_word_error_limit"] or not protected_speech_matches(take["text"], result.get("text", "")):
                failures[i] = f"expected {take['text']!r}, heard {result.get('text')!r}"
            take["recognized"] = result.get("text", "")
            progress(f"Speech quality: {i + 1}/{len(takes)} cues")
    if failures:
        # Recognition errors are not proof that a voice missed a word. Check
        # disputed takes with a different acoustic model before regenerating.
        with Worker("fallback", q, runner) as worker:
            for i in list(failures):
                take = takes[i]
                result = _cached_call(worker, checkpoints, "speech_content_whisper", {"audio": file_digest(take["path"]),
                    "text": take["text"], "model": q["models"]["fallback"]},
                    {"audio": str(take["path"]), "language": "English"})
                heard = result.get("text", "")
                if speech_error(take["text"], heard) <= q["qa_word_error_limit"] and protected_speech_matches(take["text"], heard):
                    take["recognized"] = heard
                    failures.pop(i)
                    progress(f"Automatically resolved speech-check disagreement at cue {i + 1} with independent recognition")
    with Worker("aligner", q, runner) as worker:
        for i, take in enumerate(takes):
            if i in failures:
                continue
            result = _cached_call(worker, checkpoints, "english_alignment", {"audio": file_digest(take["path"]),
                "text": take["text"], "model": q["models"]["aligner"]},
                {"audio": str(take["path"]), "text": take["text"], "language": "English"})
            items = result.get("items", [])
            try:
                if not items or any(not math.isfinite(float(x["start_time"])) or not math.isfinite(float(x["end_time"]))
                    or float(x["start_time"]) < 0 or float(x["end_time"]) <= float(x["start_time"]) for x in items):
                    raise ValueError("missing or invalid alignment")
            except (ValueError, TypeError, KeyError):
                # Content has already passed independent recognition. Preserve
                # the whole take instead of trimming from unreliable word spans.
                from .core import ffprobe_duration
                length = ffprobe_duration(take["path"], runner)
                take["alignment"] = [{"start_time": 0.0, "end_time": length, "text": take["text"]}]
                runner.diagnostic("Alignment fallback", cue=i + 1, method="whole validated take", reason="invalid word spans")
                continue
            take["alignment"] = result["items"]
    if failures:
        raise SpeechValidationFailure(failures)

def fit_takes(segments, takes, cuts, q, work, runner):
    from .core import ffprobe_duration, PipelineError, _atomic_json_write, _atomic_media_run
    _stage(runner, "visual_timing", "Planning intelligible dialogue timing")
    duration = q["media_duration"]
    clips, failed, records = [], [], []
    for i, (seg, take) in enumerate(zip(segments, takes)):
        items = take["alignment"]
        a, b = max(0, min(x["start_time"] for x in items) - .05), max(x["end_time"] for x in items) + .08
        raw_duration = ffprobe_duration(take["path"], runner)
        b = min(raw_duration, b)
        length = b - a
        available = window(segments, i, duration, cuts, q)
        tempo = choose_tempo(length, available, seg.style, q)
        if tempo is None:
            failed.append(i)
            clips.append(None)
            records.append({"cue": i + 1, "status": "unresolved", "speech_seconds": length, "available": available})
            continue
        identity = digest([file_digest(take["path"]), a, b, tempo])[:16]
        trimmed = work / f"trimmed_{i:06}_{identity}.wav"
        fitted = work / f"fitted_{i:06}_{identity}.wav"
        _cut_audio(take["path"], trimmed, a, length, runner, mono=False)
        if tempo > 1.0001:
            # Rubber Band CLI -3 selects the R3/Finer engine; -T changes tempo, not pitch.
            runner.run(["rubberband", "-3", "-T", f"{tempo:.9f}", str(trimmed), str(fitted)])
        else:
            shutil.copy2(trimmed, fitted)
        actual = ffprobe_duration(fitted, runner)
        if actual > available + .015:
            failed.append(i)
            clips.append(None)
        else:
            clips.append(fitted)
        records.append({"cue": i + 1, "tempo": tempo, "native_acceleration": 1.0,
                        "available": available, "rendered_seconds": actual, "status": "unresolved" if i in failed else "fitted"})
    _atomic_json_write(work / "timing-plan.json", {"complete": not failed, "cues": records})
    return clips, failed

def shorten_failed(segments, failed, config, q, checkpoints, glossary, runner):
    from .core import PipelineError, parse_translation_response
    from .timing import usable_rewrite
    proposals = {}
    with Worker("translator", q, runner) as translator:
        for i in failed:
            seg = segments[i]
            prompt = ("Produce one more concise spoken English translation preserving ALL meaning, tone, names, "
                "numbers, negation, realm distinctions and causality. Do not drop information. "
                'Return ONLY JSON [{"id":1,"text":"complete line"}].\n'
                f"Chinese: {seg.text}\nFaithful English: {seg.translated}\nGlossary: {json.dumps(glossary.relevant(seg.text), ensure_ascii=False)}")
            candidate = parse_translation_response(translator.call({"prompt": prompt})["text"], [1]).get(1, "")
            if usable_rewrite(seg.translated, candidate, "en"):
                proposals[i] = candidate
    if not proposals:
        return False
    manager = Worker("reviewer", q, runner) if q["review_provider"] == "local" else nullcontext(None)
    approved = False
    with manager as reviewer:
        for i, candidate in proposals.items():
            seg = segments[i]
            prompt = review_prompt(seg.text, candidate, config.context, glossary.relevant(seg.text))
            raw = reviewer.call({"prompt": prompt})["text"] if reviewer else cloud_review(prompt, q)
            row = validate_review(parse_object(raw), seg.text, candidate)
            if row["passed"] and not glossary.violations(seg.text, candidate):
                checkpoints.put("spoken_adaptation", {"source": seg.text, "faithful": seg.translated,
                    "reviewer": q["models"]["reviewer"], "glossary": glossary.revision}, {"spoken": candidate, "review": row})
                seg.translated = candidate
                approved = True
    return approved

def background_bed(audio, stems, segments, work, runner):
    from .core import _atomic_media_run
    background = work / "music-effects.wav"
    _atomic_media_run(runner, ["ffmpeg", "-y", "-loglevel", "error", "-i", str(stems["music"]), "-i", str(stems["effects"]),
        "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0:duration=first[b]", "-map", "[b]",
        "-ar", "48000", "-c:a", "pcm_f32le", str(background)], background)
    return hybrid_background(audio, background, segments, work, runner)

def mix(audio, stems, segments, clips, duration, work, config, q, runner, progress, *, source_segments=None):
    from .core import (_atomic_media_run, render_dub_timeline,
                       ffprobe_duration, PipelineError)
    bed = background_bed(audio, stems, source_segments or segments, work, runner)
    timeline = render_dub_timeline(segments, clips, duration, work, config, runner, progress)
    _stage(runner, "mixing", "Mixing voices and preserving background")
    drymix = work / "mix-premaster.wav"
    if config.ducking:
        graph = (f"[0:a]volume={config.background_volume}[bg];[1:a]volume={config.dub_volume},asplit=2[v][sc];"
                 "[bg][sc]sidechaincompress=threshold=0.06:ratio=1.8:attack=18:release=180[b];"
                 "[b][v]amix=inputs=2:normalize=0:duration=first[m]")
    else:
        graph = (f"[0:a]volume={config.background_volume}[b];[1:a]volume={config.dub_volume}[v];"
                 "[b][v]amix=inputs=2:normalize=0:duration=first[m]")
    _atomic_media_run(runner, ["ffmpeg", "-y", "-loglevel", "error", "-i", str(bed), "-i", str(timeline),
        "-filter_complex", graph, "-map", "[m]", "-ar", "48000", "-c:a", "pcm_f32le", str(drymix)], drymix)
    # Reserve headroom for AAC intersample peaks; verify delivered peaks after encoding.
    loud = f"loudnorm=I={q['loudness_lufs']}:TP={q['true_peak_db'] - .8}:LRA=11"
    measured = runner.run(["ffmpeg", "-hide_banner", "-i", str(drymix), "-af", loud + ":print_format=json", "-f", "null", "-"])
    stats = parse_object("{" + (measured.stderr or "").rsplit("{", 1)[-1])
    filters = loud + ":linear=true:measured_I={input_i}:measured_TP={input_tp}:measured_LRA={input_lra}:measured_thresh={input_thresh}:offset={target_offset}".format(**stats)
    master = work / "mix-master.wav"
    _atomic_media_run(runner, ["ffmpeg", "-y", "-loglevel", "error", "-i", str(drymix), "-af", filters,
        "-ar", "48000", "-c:a", "pcm_f32le", str(master)], master)
    from .core import _atomic_json_write
    _atomic_json_write(work / "loudness.json", {"target_lufs": q["loudness_lufs"], "true_peak_db": q["true_peak_db"], "measurement": stats})
    if abs(ffprobe_duration(master, runner) - duration) > .05:
        raise PipelineError("Final master duration differs from source; mux blocked")
    return master

def hybrid_background(original, separated, segments, work, runner):
    """48 kHz float streaming; samples outside replacement fades remain original."""
    import numpy as np
    import soundfile as sf
    from .core import _merge_dialogue_guard_intervals, PipelineError
    destination = work / "background-48k.wav"
    temporary = destination.with_suffix(".rendering.wav")
    try:
        with sf.SoundFile(original) as source, sf.SoundFile(separated) as stems:
            if source.samplerate != 48000 or stems.samplerate != 48000 or source.channels != 2 or stems.channels != 2:
                raise PipelineError("Hybrid background requires 48 kHz stereo masters")
            if abs(source.frames - stems.frames) > 2:
                raise PipelineError("Background stems differ from the source duration")
            intervals = _merge_dialogue_guard_intervals(segments, source.frames / 48000)
            with sf.SoundFile(temporary, "w", samplerate=48000, channels=2, subtype="FLOAT") as target:
                frame = 0
                while frame < source.frames:
                    runner.check_cancel()
                    count = min(16384, source.frames - frame)
                    a = source.read(count, dtype="float32", always_2d=True)
                    b = stems.read(count, dtype="float32", always_2d=True)
                    if len(b) < len(a):
                        b = np.pad(b, ((0, len(a) - len(b)), (0, 0)))
                    times = (frame + np.arange(len(a))) / 48000
                    mask = np.zeros(len(a), dtype="float32")
                    for start, end in intervals:
                        if start > times[-1]:
                            break
                        if end >= times[0]:
                            mask = np.maximum(mask, np.clip(np.minimum((times - start) / .1, (end - times) / .1), 0, 1))
                    target.write(a * (1 - mask[:, None]) + b * mask[:, None])
                    frame += count
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination

def run_quality(config, progress, runner, *, analysis=False):
    from .core import (PipelineError, download_source, validate_source_cache, source_key, _atomic_media_run,
        ffprobe_duration, _atomic_json_write, write_srt, write_vtt, _character_map_path, validate_media_streams)
    q = options(config.quality_settings)
    issues = setup_issues(q, mode=config.mode, analysis=analysis)
    if config.target_language != "en" or config.source_language not in {"zh", "auto"}:
        issues.append("This quality profile targets Chinese-to-English; existing providers remain available for other languages")
    if issues:
        raise PipelineError("Quality setup required: " + "; ".join(issues))
    environment = runtime_fingerprints(q, keys=("mlx_python", "separator_python", "torch_python") if analysis else None)
    out = Path(config.output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    key = source_key(config.source, out)
    glossary_scope = digest(config.series_id)[:16] if config.series_id else key
    glossary_root = out / (".anime_dubber_series" if config.series_id else ".anime_dubber_glossaries")
    if not q["series_glossary_path"]:
        q["series_glossary_path"] = str(glossary_root / f"{glossary_scope}-glossary.json")
    if config.series_id and not q["episode_context_path"]:
        q["episode_context_path"] = str(glossary_root / f"{glossary_scope}-episode-evidence.json")
    if Path(q["series_glossary_path"]).is_file():
        known = Glossary(q["series_glossary_path"], config.glossary)
        config.glossary.update({term: row["english"] for term, row in known.entries.items() if row.get("confirmed")})
    shared = out / ".anime_dubber_work" / key
    shared.mkdir(parents=True, exist_ok=True)
    video = download_source(config.source, shared, runner, progress)
    if validate_source_cache(video, shared, progress):
        callback = getattr(runner, "source_changed", None)
        if callback:
            callback()
    identity = file_digest(video)
    shared = shared / ("quality_" + identity[:16])
    shared.mkdir(parents=True, exist_ok=True)
    version = out / "versions" / config.version_id if config.version_id else out
    version.mkdir(parents=True, exist_ok=True)
    work = shared / ("dub_" + (config.version_id or "default"))
    work.mkdir(parents=True, exist_ok=True)
    checkpoints = Checkpoints(shared / "checkpoints", identity, config, runner, environment)
    version_cp = Checkpoints(work / "checkpoints", identity, config, runner, environment)
    _atomic_json_write(work / "run-manifest.json", {"profile": "quality_v1", "source_sha256": identity,
        "models": q["models"], "runtime_versions": environment, "settings": q, "hardware_verified": False, "complete": False})
    audio = shared / "original-48k.wav"
    from .core import _complete_wav
    from .quality_export import source_duration, mux_preserving_video
    duration = source_duration(video, runner)
    # Audio-only duration can be shorter than the video. Pad silence rather than
    # truncate the source frames; use the complete original container timeline.
    if config.force or not config.resume or not _complete_wav(audio, minimum_seconds=duration * .999):
        _atomic_media_run(runner, ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn",
            "-af", "apad", "-t", str(duration), "-ac", "2", "-ar", "48000", "-c:a", "pcm_f32le", str(audio)], audio)
    if shutil.disk_usage(shared).free < duration * 48000 * 2 * 4 * 8:
        raise PipelineError("Insufficient workspace disk for streamed stems, voices and checkpoints")
    _publish(runner, "source_video", video, "zh")
    stems = separation(audio, shared, q, config, checkpoints, runner, progress)
    segments, cuts = source_analysis(video, audio, stems, config, q, shared, checkpoints, runner, progress)
    config.source_language = "zh"
    source_srt, source_vtt = out / f"{key}_zh.srt", out / f"{key}_zh.vtt"
    write_srt(segments, source_srt)
    write_vtt(segments, source_vtt)
    for kind, path in (("chinese_srt", source_srt), ("chinese_vtt", source_vtt)):
        _publish(runner, kind, path, "zh")
    # A different diarization backend must never replace legacy manual character edits.
    char_path = out / f"{key}_quality_characters.json"
    profiles = cast(segments, config, q, char_path, runner)
    snapshot_map = version / f"{key}_characters.json" if config.version_id else work / "version-characters.json"
    _atomic_json_write(snapshot_map, {"version": 2, "characters": list(profiles.values()),
        "pipeline_profile": "quality_v1", "source_key": key,
        "segments": [s.to_dict() for s in segments], "source_sha256": identity,
        "speaker_backend": "pyannote-community-1", "series_id": config.series_id})
    _publish(runner, "character_map", snapshot_map, "en")
    results = {"chinese_srt": source_srt, "chinese_vtt": source_vtt, "character_map": snapshot_map}
    if analysis:
        return results
    glossary_path = Path(q["series_glossary_path"]).expanduser()
    glossary = Glossary(glossary_path, config.glossary)
    partial = version / f"{key}_en.partial.srt"
    def save_partial():
        done = [s for s in segments if s.translated]
        if done:
            write_srt(done, partial, translated=True)
            _publish(runner, "partial_translated_srt", partial, "en")
    reviews, review_path = localize(segments, config, q, version_cp, glossary, runner, progress, save_partial)
    target_srt, target_vtt = version / f"{key}_en.srt", version / f"{key}_en.vtt"
    def subtitles():
        write_srt(segments, target_srt, translated=True)
        write_vtt(segments, target_vtt, translated=True)
        _publish(runner, "translated_srt", target_srt, "en")
        _publish(runner, "translated_vtt", target_vtt, "en")
    subtitles()
    results.update({"translated_srt": target_srt, "english_srt": target_srt, "translated_vtt": target_vtt, "review_report": review_path})
    _publish(runner, "review_report", review_path, "en")
    _publish(runner, "glossary", glossary_path, "zh")
    partial.unlink(missing_ok=True)
    _atomic_json_write(work / "faithful-dialogue.json", {"cues": reviews, "glossary_revision": glossary.revision})
    if q["episode_context_path"]:
        from .application.project_lock import folder_lock
        ledger_path = Path(q["episode_context_path"]).expanduser()
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with folder_lock(ledger_path.parent):
            ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"episodes": {}}
            ledger["episodes"][identity] = {"source_sha256": identity, "cues": reviews}
            _atomic_json_write(ledger_path, ledger)
    if config.mode == "subtitles":
        return results
    runtime_q = {**q, "media_duration": duration}
    original_segments = list(segments)
    delivery_hints = {}
    for attempt in range(3):
        takes = generate_checked_speech(segments, profiles, glossary, config, q, work, version_cp, runner, progress,
                                       delivery_hints=delivery_hints)
        _atomic_json_write(snapshot_map, {"version": 2, "characters": list(profiles.values()),
            "pipeline_profile": "quality_v1", "source_key": key,
            "segments": [s.to_dict() for s in original_segments], "source_sha256": identity,
            "speaker_backend": "pyannote-community-1", "series_id": config.series_id})
        clips, failed = fit_takes(segments, takes, cuts, runtime_q, work, runner)
        _publish(runner, "timing_plan", work / "timing-plan.json", "en")
        if not failed:
            break
        if attempt == 0 and any(profiles[segments[i].speaker_id]["tts_provider"] == "qwen" for i in failed):
            for i in failed:
                if profiles[segments[i].speaker_id]["tts_provider"] == "qwen":
                    delivery_hints[i] = ("Keep the expressive style but reduce pauses between phrases; pronounce all words clearly."
                        if segments[i].style in {"whispering", "shouting"} else
                        "Deliver fluently at a brisk conversational pace with shorter pauses, preserving every word and its emphasis.")
            progress("Automatically adjusting voice delivery to the original video timing")
            continue
        if attempt == 2 or not shorten_failed(segments, failed, config, q, version_cp, glossary, runner):
            if q["allow_rescue_speed"]:
                rescue_q = {**runtime_q, "normal_speed": q["maximum_speed"], "urgent_speed": q["maximum_speed"]}
                clips, failed = fit_takes(segments, takes, cuts, rescue_q, work, runner)
            if failed:
                raise PipelineError("Automatic timing recovery could not fit cues " + ", ".join(str(i + 1) for i in failed)
                                    + "; original video preserved, subtitles and voices saved")
            break
        subtitles()
    rendered = [ffprobe_duration(path, runner) for path in clips]
    validate_schedule(segments, rendered, cuts)
    from .visual_sync import align_dub_to_visible_speech
    hinted = align_dub_to_visible_speech(video, segments, clips, work, runner, progress,
        total_duration=duration, resume=config.resume, force=config.force)
    timeline_segments = list(segments)
    # Mouth motion is soft evidence. Reject large shifts or any hard collision/cut.
    for i, candidate in enumerate(hinted):
        if abs(candidate.start - segments[i].start) > .15 or candidate.start < 0:
            continue
        trial = list(timeline_segments)
        trial[i] = candidate
        try:
            validate_schedule(trial, rendered, cuts)
        except PipelineError:
            continue
        if candidate.start + rendered[i] <= duration + .015:
            timeline_segments = trial
    # Re-recognize after time stretching, not only before it.
    fitted_takes = [{**take, "path": clip, "identity": {**take["identity"], "fitted_sha256": file_digest(clip)}}
                   for take, clip in zip(takes, clips)]
    speech_qa(fitted_takes, q, version_cp, runner, progress)
    # English captions follow the measured fitted speech, including borrowed
    # silence. Keep original Chinese/source windows intact for the background.
    from dataclasses import replace
    timeline_segments = [replace(seg, end=min(duration, seg.start + length))
                         for seg, length in zip(timeline_segments, rendered)]
    # Keep source replacement windows at their original positions while placing
    # English at accepted visual hints. Only the English subtitle timing changes.
    master = mix(audio, stems, timeline_segments, clips, duration, work, config, q, runner, progress,
                 source_segments=original_segments)
    write_srt(timeline_segments, target_srt, translated=True)
    write_vtt(timeline_segments, target_vtt, translated=True)
    encoded = work / "dub-audio.m4a"
    _atomic_media_run(runner, ["ffmpeg", "-y", "-loglevel", "error", "-i", str(master), "-c:a", "aac", "-b:a", "256k", str(encoded)], encoded)
    final = version / f"{key}_EN_DUB.mp4"
    final = mux_preserving_video(video, encoded, final, runner, progress)
    validate_media_streams(final, runner)
    # Measure the delivered AAC stream: encoding can introduce intersample peaks.
    checked = runner.run(["ffmpeg", "-hide_banner", "-i", str(final), "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    final_stats = parse_object("{" + (checked.stderr or "").rsplit("{", 1)[-1])
    if float(final_stats["input_tp"]) > q["true_peak_db"] + .05:
        raise PipelineError("Encoded output exceeds true-peak target; master/checkpoints saved, output not marked complete")
    audio_export = version / f"{key}_EN_DUB.m4a"
    shutil.copy2(encoded, audio_export)
    results.update({"dubbed_video": final, "dub_audio": audio_export})
    for kind, path in (("dubbed_video", final), ("dub_audio", audio_export)):
        _publish(runner, kind, path, "en")
    _atomic_json_write(work / "run-manifest.json", {"profile": "quality_v1", "source_sha256": identity,
        "models": q["models"], "runtime_versions": environment, "settings": q, "hardware_verified": False, "complete": True,
        "delivered_loudness": final_stats, "artifacts": {k: str(v) for k, v in results.items()}})
    return results
