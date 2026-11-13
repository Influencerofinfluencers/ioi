"""One small wrapper so every step can use Claude or an open-source model."""
import json
import re

import httpx

from . import config


class LLMError(Exception):
    pass


def complete(system: str, user: str, model: str, max_tokens: int = 2000, provider: str | None = None) -> str:
    if (provider or config.LLM_PROVIDER) == "openai":
        return _openai(system, user, model, max_tokens)
    return _anthropic(system, user, model, max_tokens)


def _anthropic(system, user, model, max_tokens):
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY missing")
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _openai(system, user, model, max_tokens):
    """Any OpenAI-compatible endpoint: OpenRouter, Groq, Together, vLLM, Ollama..."""
    if not config.OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY missing")
    r = httpx.post(
        f"{config.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse_json(textout: str):
    """Pull JSON out of a model reply. Tolerates ``` fences, text around it and a
    reply that was cut off: complete objects of a truncated array are kept."""
    cleaned = re.sub(r"```(?:json)?", "", textout).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    # salvage: decode every complete {...} object one by one
    dec, items, i = json.JSONDecoder(), [], cleaned.find("{")
    while i != -1:
        try:
            obj, nxt = dec.raw_decode(cleaned, i)
            items.append(obj)
            i = cleaned.find("{", nxt)
        except json.JSONDecodeError:
            i = cleaned.find("{", i + 1)
    if items:
        return items
    raise LLMError(f"model did not return JSON: {textout[:200]}")
