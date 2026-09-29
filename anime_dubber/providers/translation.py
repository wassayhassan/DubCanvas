from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List


def ollama_generate(
    *,
    base_url: str,
    model: str,
    prompt: str,
    timeout: int = 300,
) -> str:
    url = (base_url or "http://127.0.0.1:11434").rstrip("/") + "/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.15},
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {url}. Start Ollama or choose another translation provider."
        ) from exc
    if not isinstance(body, dict):
        raise RuntimeError("Ollama returned an invalid response.")
    text = str(body.get("response") or "").strip()
    if not text:
        error = body.get("error")
        raise RuntimeError(f"Ollama returned no translation text{': ' + str(error) if error else ''}.")
    return text


def translate_with_ollama(
    *,
    batches: Iterable[tuple[List[int], str]],
    base_url: str,
    model: str,
    parse_batch,
    single_prompt,
    cancel_check,
    progress,
    on_batch=None,
) -> Dict[int, str]:
    """Run structured batched translations through a local Ollama server."""
    translated: Dict[int, str] = {}
    batches = list(batches)
    total = sum(len(ids) for ids, _ in batches)
    done = 0

    for ids, prompt in batches:
        cancel_check()
        parsed: Dict[int, str] = {}
        retry_prompt = prompt
        for _attempt in range(3):
            raw = ollama_generate(base_url=base_url, model=model, prompt=retry_prompt)
            parsed.update(parse_batch(raw, ids))
            if len(parsed) == len(ids):
                break
            retry_prompt += (
                f"\nYour previous response missed these ids: {[idx for idx in ids if idx not in parsed]}. "
                "Return exactly the requested JSON array with the same ids and no commentary."
            )

        for idx in ids:
            if idx in parsed and parsed[idx]:
                continue
            cancel_check()
            raw = ollama_generate(
                base_url=base_url,
                model=model,
                prompt=single_prompt(idx),
                timeout=180,
            ).strip()
            cleaned = raw.strip().strip('"')
            if "\n" in cleaned:
                cleaned = next((line.strip() for line in cleaned.splitlines() if line.strip()), "")
            parsed[idx] = cleaned

        translated.update({idx: text for idx, text in parsed.items() if text})
        if on_batch:
            on_batch(ids, parsed)
        done += len(ids)
        progress(f"Ollama translation: {min(done, total)}/{total} lines")

    return translated
