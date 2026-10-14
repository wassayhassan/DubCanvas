"""Evidence-linked terminology, complete cue translation and independent meaning gates."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import urllib.request

from .quality_models import digest

def parse_object(text):
    text = re.sub(r"<think>.*?</think>", "", str(text), flags=re.S).strip()
    decoder = json.JSONDecoder()
    for n, char in enumerate(text):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(text[n:])
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
    raise ValueError("Model did not return a valid review object")

class Glossary:
    def __init__(self, path, seed):
        self.path = Path(path)
        self.entries = {}
        self.ranks = []
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = value.get("entries", {})
            self.ranks = value.get("realm_order", [])
        for chinese, english in seed.items():
            self.entries.setdefault(chinese, {"source": chinese, "english": english,
                "aliases": [], "kind": "term", "pronunciation": "", "evidence": [], "confirmed": True})

    @property
    def revision(self):
        return digest({"entries": self.entries, "realm_order": self.ranks})

    def relevant(self, source):
        return {key: value for key, value in self.entries.items()
                if key in source or any(alias in source for alias in value.get("aliases", []))}

    def violations(self, source, target):
        return [key for key, row in self.relevant(source).items()
                if row.get("confirmed") and row["english"].casefold() not in target.casefold()]

    def learn(self, terms, source, target, evidence):
        # Discovery requires an independent reviewer and exact text evidence.
        for row in terms:
            if not isinstance(row, dict):
                continue
            key, english = str(row.get("source", "")), str(row.get("english", ""))
            if not key or key not in source or not english or english.casefold() not in target.casefold():
                continue
            existing = self.entries.get(key)
            if existing and existing["english"].casefold() != english.casefold():
                raise ValueError(f"Glossary conflict for {key}: {existing['english']} / {english}")
            if not existing:
                self.entries[key] = {"source": key, "english": english, "kind": row.get("kind", "term"),
                    "aliases": row.get("aliases", []), "pronunciation": row.get("pronunciation", ""),
                    "evidence": [evidence], "confirmed": False}
            elif evidence not in existing.get("evidence", []):
                existing.setdefault("evidence", []).append(evidence)
                existing["confirmed"] = existing.get("confirmed", False) or len(existing["evidence"]) >= 2

    def save(self):
        from .core import _atomic_json_write
        from .application.project_lock import folder_lock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with folder_lock(self.path.parent):
            if self.path.exists():
                latest = json.loads(self.path.read_text(encoding="utf-8"))
                for key, row in latest.get("entries", {}).items():
                    if key in self.entries and self.entries[key]["english"] != row["english"]:
                        raise ValueError(f"Glossary changed during processing at {key}; retry with the new terminology")
                    self.entries.setdefault(key, row)
            _atomic_json_write(self.path, {"version": 1, "revision": self.revision,
                                        "entries": self.entries, "realm_order": self.ranks})

    def voice_text(self, target):
        for row in sorted(self.entries.values(), key=lambda x: len(x["english"]), reverse=True):
            if row.get("pronunciation") and row.get("confirmed"):
                target = re.sub(r"\b" + re.escape(row["english"]) + r"\b", row["pronunciation"], target)
        return target

def cloud_review(prompt, q):
    if not q["allow_cloud"]:
        raise ValueError("Cloud text upload is disabled")
    key = os.getenv("DUBCANVAS_QWEN_API_KEY", "")
    if not key:
        raise ValueError("Set DUBCANVAS_QWEN_API_KEY in the backend environment")
    url = q["cloud_review_url"].rstrip("/") + "/chat/completions"
    if not url.startswith("https://"):
        raise ValueError("Cloud review requires HTTPS")
    request = urllib.request.Request(url, data=json.dumps({"model": q["cloud_review_model"],
        "messages": [{"role": "user", "content": prompt}], "temperature": 0,
        "max_tokens": q["max_output_tokens"]}).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=min(300, q["worker_timeout"])) as response:
        result = json.load(response)
    return result["choices"][0]["message"]["content"]

def review_prompt(source, target, context, glossary, language="English"):
    return (
        "Independently verify the target against the ORIGINAL Chinese. Do not merely polish English. "
        "Check every proposition: agent, action, object, negation, modality, numbers, causality, rank, "
        "names, honorifics, tone, omitted or invented dialogue and speaker attribution. "
        "Personal names use stable pinyin; translate functional titles; never impose a universal realm order. "
        "Return ONLY JSON: {\"passed\":true|false,\"errors\":[{\"category\":\"...\","
        "\"source_evidence\":\"exact source quote\",\"target_evidence\":\"quote or empty for omission\","
        "\"explanation\":\"English explanation\"}],\"correction\":\"complete faithful translation\","
        "\"terms\":[{\"source\":\"exact term\",\"english\":\"exact target spelling\",\"kind\":\"name|realm|sect|technique|artifact|title\"}]}. "
        "passed=true requires zero meaning errors. Uncertain meaning must be passed=false. "
        f"Target language: {language}\nSource: {source}\nTarget: {target}\n"
        f"Relevant glossary and realm relationships: {json.dumps(glossary, ensure_ascii=False)}\nContext: {context}")

def validate_review(row, source, target):
    if not isinstance(row.get("passed"), bool) or not isinstance(row.get("errors"), list):
        raise ValueError("Review lacks explicit pass decision and error list")
    if row["passed"] and row["errors"]:
        raise ValueError("Contradictory review: passed with meaning errors")
    for error in row["errors"]:
        if not isinstance(error, dict) or not error.get("category") or not error.get("explanation"):
            raise ValueError("Review error lacks category/explanation")
        evidence = error.get("source_evidence", "")
        if not evidence or evidence not in source:
            raise ValueError("Review error lacks valid Chinese source evidence")
        quote = error.get("target_evidence", "")
        if quote and quote not in target:
            raise ValueError("Review target evidence is not present in the translation")
    return row

def translate_prompt(source, context, glossary, language="English", problem=""):
    return (f"Translate this Chinese dialogue into faithful natural {language}. "
        "Preserve every fact, number, negation, rank, name, relationship and emotional intent. "
        "Do not shorten by omitting plot information. Personal names use consistent pinyin. "
        "Return ONLY the complete translated dialogue, no commentary.\n"
        f"Terminology: {json.dumps(glossary, ensure_ascii=False)}\n"
        f"Scene and episode context: {context}\nRepair requirements: {problem}\nSource: {source}")

def localize(segments, config, q, checkpoints, glossary, runner, progress, partial_callback,
             worker_factory=None):
    from .core import PipelineError, translation_is_usable
    from .languages import target_name
    from .quality_runtime import Worker
    worker_factory = worker_factory or Worker
    from contextlib import nullcontext
    source_rows = [s.to_dict() for s in segments]
    translations = []
    with worker_factory("translator", q, runner) as translator:
        runner.set_stage("translating", "Translating complete dialogue")
        for i, seg in enumerate(segments):
            runner.check_cancel()
            context = scene_context(source_rows, i, config.context)
            context = episode_context(q, glossary, seg.text, context)
            inputs = {"model": q["models"]["translator"], "source": seg.text,
                      "context": context, "glossary": glossary.revision, "language": config.target_language,
                      "adapter": q["translation_adapter_sha256"]}
            cached = checkpoints.get("translation", inputs)
            text = cached.get("text", "") if cached else ""
            if not translation_is_usable(seg.text, text, "zh", config.target_language):
                for attempt in range(3):
                    raw = translator.call({"prompt": translate_prompt(seg.text, context,
                        glossary.relevant(seg.text), target_name(config.target_language),
                        "Previous output was invalid; translate all dialogue" if attempt else "")})
                    text = raw["text"].strip().strip('"')
                    if translation_is_usable(seg.text, text, "zh", config.target_language):
                        break
                else:
                    partial_callback()
                    raise PipelineError(f"Translation incomplete at cue {i + 1} ({seg.start:.2f}s). Source and completed translations saved")
                checkpoints.put("translation", inputs, {"text": text})
            seg.translated = text
            translations.append(inputs)
            partial_callback()
            progress(f"LLM translation: {i + 1}/{len(segments)} lines")

    # Review every cue; never load translator and reviewer together.
    reviews = []
    for cycle in range(3):
        failing = []
        manager = worker_factory("reviewer", q, runner) if q["review_provider"] == "local" else nullcontext(None)
        with manager as reviewer:
            runner.set_stage("reviewing", "Checking meaning against Chinese")
            reviews = []
            for i, seg in enumerate(segments):
                runner.check_cancel()
                context = scene_context(source_rows, i, config.context)
                context = episode_context(q, glossary, seg.text, context)
                inputs = {"model": q["models"]["reviewer"] if reviewer else q["cloud_review_model"],
                    "provider": q["review_provider"], "source": seg.text, "target": seg.translated,
                    "glossary": glossary.revision, "context": context, "language": config.target_language}
                row = checkpoints.get("meaning_review", inputs)
                if row is None:
                    prompt = review_prompt(seg.text, seg.translated, context,
                        {"terms": glossary.relevant(seg.text), "realm_order": glossary.ranks}, target_name(config.target_language))
                    for attempt in range(2):
                        try:
                            raw = reviewer.call({"prompt": prompt})["text"] if reviewer else cloud_review(prompt, q)
                            row = validate_review(parse_object(raw), seg.text, seg.translated)
                            break
                        except ValueError as exc:
                            if attempt:
                                raise PipelineError(f"Invalid meaning review for cue {i + 1}: {exc}") from exc
                            prompt += "\nYour JSON/evidence was invalid. Return the exact schema with verbatim evidence."
                    checkpoints.put("meaning_review", inputs, row)
                violations = glossary.violations(seg.text, seg.translated)
                if not row["passed"] or violations:
                    failing.append((i, row, violations))
                else:
                    try:
                        glossary.learn(row.get("terms", []), seg.text, seg.translated,
                                       f"{checkpoints.identity}:{i + 1}")
                    except ValueError as exc:
                        # A newly discovered spelling conflict is repairable, not
                        # permission to overwrite established project terminology.
                        row = {**row, "passed": False, "errors": row["errors"] + [{
                            "category": "terminology_conflict", "source_evidence": seg.text,
                            "target_evidence": seg.translated, "explanation": str(exc)}]}
                        failing.append((i, row, ["inconsistent discovered terminology"]))
                reviews.append({"cue": i + 1, "source": seg.text, "faithful_translation": seg.translated,
                                "start": seg.start, "end": seg.end, **row, "glossary_violations": violations})
                progress(f"Meaning review: {i + 1}/{len(segments)} cues (pass {cycle + 1})")
        glossary.save()
        from .core import _atomic_json_write
        report_path = checkpoints.directory / "meaning-review.json"
        _atomic_json_write(report_path, {"complete": not failing, "total_cues": len(segments),
            "flagged_cues": len(failing), "flags": reviews, "model": q["models"]["reviewer"], "cycle": cycle})
        if not failing:
            return reviews, report_path
        if cycle == 2:
            partial_callback()
            raise PipelineError("Meaning review unresolved at cues " + ", ".join(str(i + 1) for i, _, _ in failing)
                                + ". Subtitles and evidence are saved; no dub generated")
        with worker_factory("translator", q, runner) as translator:
            runner.set_stage("translating", "Repairing specific meaning errors")
            for i, row, violations in failing:
                seg = segments[i]
                problem = json.dumps({"errors": row["errors"], "glossary_violations": violations}, ensure_ascii=False)
                for attempt in range(3):
                    text = translator.call({"prompt": translate_prompt(seg.text,
                        episode_context(q, glossary, seg.text, scene_context(source_rows, i, config.context)), glossary.relevant(seg.text),
                        target_name(config.target_language), problem + ("\nPrevious repair was incomplete; translate every word." if attempt else ""))})["text"].strip().strip('"')
                    if translation_is_usable(seg.text, text, "zh", config.target_language):
                        break
                else:
                    partial_callback()
                    raise PipelineError(f"Automatic translation repair exhausted at cue {i + 1}; completed work saved")
                seg.translated = text
                checkpoints.put("translation", translations[i], {"text": text})
                partial_callback()
    raise AssertionError("Bounded review loop exhausted")

def scene_context(rows, index, context):
    # Adjacent exact dialogue is safer than an ungrounded generated story summary.
    current = rows[index]
    neighboring = [row for row in rows[max(0, index - 4):index + 5]
                   if abs(row["start"] - current["start"]) < 90]
    return json.dumps({"project_context": context[:1500], "neighboring_source": neighboring}, ensure_ascii=False)

def episode_context(q, glossary, source, scene):
    path = q["episode_context_path"]
    if not path or not Path(path).exists():
        return scene
    ledger = json.loads(Path(path).read_text(encoding="utf-8"))
    entities = [key for key, row in glossary.relevant(source).items()
                if row.get("kind") in {"name", "realm", "sect", "technique", "artifact"}]
    related = []
    for identity, episode in ledger.get("episodes", {}).items():
        for cue in episode.get("cues", []):
            if any(entity in cue.get("source", "") for entity in entities):
                related.append({"source_sha256": identity, "cue": cue.get("cue"),
                    "source": cue["source"], "translation": cue.get("faithful_translation", "")})
    return scene + "\nEvidence from previous episodes (do not invent facts): " + json.dumps(related[-3:], ensure_ascii=False)
