"""Generate bounded, measurable rewrites for dub lines that overrun the next cue."""
from __future__ import annotations

import gc
import json
import math
import re
from .languages import source_name, target_name
from .prompts import render_prompt


def _anchors(text: str) -> list[str]:
    """Keep obvious proper names and numbers from the approved translation."""
    names = [re.sub(r"^(?:The|A|An)\s+", "", name)
             for name in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text)]
    numbers = re.findall(r"\b\d+(?:[.,]\d+)*\b", text)
    return list(dict.fromkeys(names + numbers))


def usable_rewrite(original: str, candidate: str, target_language: str = "en") -> bool:
    candidate = candidate.strip().strip('"')
    if not candidate or candidate.casefold() == original.strip().casefold():
        return False
    if len(candidate) >= len(original.strip()) or (target_language not in {"zh", "ja"}
                                                   and re.search(r"[\u3400-\u9fff]", candidate)):
        return False
    if any(anchor.casefold() not in candidate.casefold() for anchor in _anchors(original)):
        return False
    if any(mark in candidate for mark in ("```", "[{'", "\n")):
        return False
    return True


class TimingRewriter:
    """Load at most one local LLM at a time; use the configured Ollama server when selected."""

    def __init__(self, config, provider: str = ""):
        self.config = config
        self.provider = provider or config.translation
        self._name = ""
        self._model = None
        self._tokenizer = None

    def candidate(self, source: str, original: str, duration: float, available: float,
                  attempt: int, rejected: list[str], runner) -> str:
        from .core import _response_text, glossary_string, parse_translation_response

        runner.check_cancel()
        ideographic = self.config.target_language in {"zh", "ja"}
        words = len(original) if ideographic else len(original.split())
        if not words:
            return ""
        budget = max(1, min(words - 1, math.floor(words * available / max(duration, .1) * (.84 - attempt * .1))))
        anchors = _anchors(original)
        target = target_name(self.config.target_language)
        source_lang = source_name(self.config.source_language)
        prompt = (
            f"Rewrite ONE spoken {target} dub line so the voice fits before the next speaker. "
            f"Preserve the facts, intent, and names from the {source_lang} source and existing translation. "
            "Use natural compact speech; do not summarize the scene or invent details. "
            f"Keep these exact names and numbers: {json.dumps(anchors, ensure_ascii=False)}. "
            f"The existing voice took {duration:.2f}s, but only {available:.2f}s is available. "
            f"Use at most {budget} {'characters' if ideographic else 'spoken words'}. Avoid these failed phrasings: {json.dumps(rejected, ensure_ascii=False)}. "
            'Return ONLY a JSON array: [{"id": 1, "text": "short line"}].\n'
            f"Context: {self.config.context}\nGlossary: {glossary_string(self.config.glossary)}\n"
            f"{source_lang} source: {source}\nCurrent translation: {original}"
        )
        if self.provider == "ollama":
            from .providers.translation import ollama_generate
            raw = ollama_generate(base_url=self.config.ollama_url, model=self.config.ollama_model,
                                  prompt=prompt, timeout=180)
        else:
            from mlx_lm import generate, load
            names = list(dict.fromkeys((self.config.llm_model, self.config.review_model)))
            name = names[0] if attempt < 2 or len(names) == 1 else names[1]
            if name != self._name:
                self._model = self._tokenizer = None
                gc.collect()
                runner.check_cancel()
                self._model, self._tokenizer = load(name)
                self._name = name
            tokenizer = self._tokenizer
            prompt = render_prompt(tokenizer, prompt)
            raw = _response_text(generate(self._model, tokenizer, prompt=prompt,
                                          max_tokens=180, verbose=False))
        candidate = parse_translation_response(raw, [1]).get(1, "").strip()
        return candidate if usable_rewrite(original, candidate, self.config.target_language) and candidate not in rejected else ""
