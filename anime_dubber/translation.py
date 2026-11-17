"""Contextual per-cue translation with resumable, model-specific checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from .languages import source_name, target_name
from .prompts import render_prompt

PROMPT_VERSION = 3


def cue_context(segments, index):
    seg = segments[index]
    return {
        "text": seg.text,
        "speaker": seg.speaker_id or "unknown",
        "style": seg.style,
        "seconds": round(max(0.1, seg.end - seg.start), 2),
        "syllable_budget": max(1, round(max(0.1, seg.end - seg.start) * 4)),
        "neighbors": [
            {"text": s.text, "speaker": s.speaker_id or "unknown"}
            for s in segments[max(0, index - 3) : index + 4]
        ],
    }


def cloud_generate(config, prompt, temperature):
    from .core import PipelineError

    if config.cloud_translation_consent is not True:
        raise PipelineError(
            "Enable cloud translation consent before sending dialogue to a provider"
        )
    endpoint = config.cloud_translation_url.rstrip("/")
    if not endpoint.startswith("https://") or not config.cloud_translation_model:
        raise PipelineError(
            "Cloud translation requires an HTTPS chat-completions endpoint and model"
        )
    key = config.cloud_translation_api_key or os.getenv(
        "DUBCANVAS_TRANSLATION_API_KEY", ""
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(
            {
                "model": config.cloud_translation_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            }
        ).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.load(response)
    return body["choices"][0]["message"]["content"]


def translate(segments, config, work, runner, progress, provider):
    from .core import (
        _atomic_json_write,
        parse_translation_response,
        translation_is_usable,
        PipelineError,
        _response_text,
    )

    runner.set_stage("translating", "Translating with speaker and timing context")
    settings = {
        "prompt_version": PROMPT_VERSION,
        "provider": provider,
        "model": config.llm_model
        if provider == "llm"
        else config.ollama_model
        if provider == "ollama"
        else config.cloud_translation_model,
        "endpoint": config.ollama_url
        if provider == "ollama"
        else config.cloud_translation_url
        if provider == "cloud"
        else "",
        "source": config.source_language,
        "target": config.target_language,
        "glossary": config.glossary,
        "context": config.context,
    }
    identity = hashlib.sha256(
        json.dumps(settings, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:20]
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"translation_lines_{provider}_{identity}.json"
    cache = {}
    if config.resume and not config.force and path.exists():
        try:
            cache = json.loads(path.read_text())
            if not isinstance(cache, dict):
                cache = {}
        except (ValueError, OSError):
            pass
    rows = [cue_context(segments, i) for i in range(len(segments))]
    keys = [
        hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        for row in rows
    ]
    pending = []
    for i, seg in enumerate(segments):
        value = cache.get(keys[i], "")
        if isinstance(value, str) and translation_is_usable(
            seg.text, value, config.source_language, config.target_language
        ):
            seg.translated = value
        else:
            pending.append(i)
    if not pending:
        return segments
    if provider == "llm":
        from mlx_lm import load, generate
        from mlx_lm.sample_utils import make_sampler

        runner.check_cancel()
        model, tokenizer = load(config.llm_model)
    for off in range(0, len(pending), 12):
        missing = pending[off : off + 12]
        for attempt in range(3):
            runner.check_cancel()
            prompt = (
                f"Translate {source_name(config.source_language)} dialogue to natural {target_name(config.target_language)}. "
                'Return ONLY a JSON array of {"id": number, "text": "translation"} for the requested IDs. '
                "Neighbors are context ONLY, never extra output. Speaker tags identify who is speaking; do not invent gender. "
                "Preserve names, numbers, negation and meaning. Use the syllable budget as a soft spoken-length target; "
                "never omit information merely to fit. "
                f"Context: {config.context}\nGlossary: {json.dumps(config.glossary, ensure_ascii=False)}\n"
                f"Attempt {attempt + 1}; translate all requested lines, do not echo source text.\n"
                f"Input: {json.dumps([dict(rows[i], id=i) for i in missing], ensure_ascii=False)}"
            )
            temperature = 0.1 + attempt * 0.2
            if provider == "llm":
                raw = _response_text(
                    generate(
                        model,
                        tokenizer,
                        prompt=render_prompt(tokenizer, prompt),
                        sampler=make_sampler(temp=temperature),
                        max_tokens=2200,
                        verbose=False,
                    )
                )
            elif provider == "ollama":
                from .providers.translation import ollama_generate

                raw = ollama_generate(
                    base_url=config.ollama_url,
                    model=config.ollama_model,
                    prompt=prompt,
                    temperature=temperature,
                )
            else:
                raw = cloud_generate(config, prompt, temperature)
            parsed = parse_translation_response(raw, missing)
            for i, value in parsed.items():
                if translation_is_usable(
                    segments[i].text,
                    value,
                    config.source_language,
                    config.target_language,
                ):
                    segments[i].translated = value
                    cache[keys[i]] = value
            _atomic_json_write(path, cache)
            missing = [i for i in missing if keys[i] not in cache]
            if not missing:
                break
        if missing:
            raise PipelineError(
                f"Translation failed for lines {[i + 1 for i in missing]}; completed lines are checkpointed"
            )
        progress(f"Translation: {min(off + 12, len(pending))}/{len(pending)} lines")
    return segments
