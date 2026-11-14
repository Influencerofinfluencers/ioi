"""One small wrapper so every step can use Claude or an open-source model."""
import json
import re

import httpx

from . import config


class LLMError(Exception):
    pass


_warned: set[str] = set()


def _one(system, user, model, max_tokens, provider):
    if provider == "openai":
        return _openai(system, user, model, max_tokens)
    return _anthropic(system, user, model, max_tokens)


def complete(system: str, user: str, model: str, max_tokens: int = 2000, provider: str | None = None) -> str:
    """Primary model first; on any failure (outage, rate limit, empty credit) the open-source
    fallbacks take over one by one (OpenRouter or any OpenAI-compatible endpoint)."""
    chain = [((provider or config.LLM_PROVIDER), model)]
    if config.OPENAI_API_KEY:
        chain += [("openai", m) for m in config.LLM_FALLBACK_MODELS if m and m != model]
    errors = []
    for i, (prov, mdl) in enumerate(chain):
        try:
            out = _one(system, user, mdl, max_tokens, prov)
            if not out or not out.strip():
                raise LLMError("empty reply")
            if i > 0 and mdl not in _warned:
                _warned.add(mdl)
                _notify(f"IOI: AI fallback active - {chain[0][1]} failed ({errors[0][:120]}), now using {mdl}")
            return out
        except Exception as e:  # noqa: BLE001 - try the next model
            errors.append(f"{mdl}: {str(e)[:200]}")
    raise LLMError("all models failed: " + " | ".join(errors))


def _notify(text: str):
    try:
        from . import notify

        notify.status(text)
    except Exception:  # noqa: BLE001
        pass


def _anthropic(system, user, model, max_tokens):
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY missing")
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=120, max_retries=1)  # never hang the loop
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
