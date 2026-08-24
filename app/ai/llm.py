"""Open-source LLM client.

The AI layer runs entirely on open-source models — no Anthropic key and no
per-call cost. It speaks the OpenAI-compatible Chat Completions API, which
Ollama, Groq and OpenRouter all expose, so a single implementation covers
every supported provider:

    LLM_PROVIDER=ollama            -> http://localhost:11434/v1   (free, local)
    LLM_PROVIDER=groq              -> https://api.groq.com/openai/v1
    LLM_PROVIDER=openrouter        -> https://openrouter.ai/api/v1
    LLM_PROVIDER=openai_compatible -> any self-hosted gateway (vLLM, LM Studio…)

There is no Anthropic dependency and no Anthropic API key. If the model
endpoint is unreachable, `complete()` raises LLMUnavailable; callers catch it
and fall back to deterministic templates so the product still works offline.
"""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings

log = logging.getLogger("leadsystem.llm")


class LLMUnavailable(RuntimeError):
    """Raised when no model endpoint could be reached."""


def _headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    # OpenRouter appreciates these; harmless elsewhere.
    if settings.llm_provider == "openrouter":
        headers["HTTP-Referer"] = settings.app_base_url
        headers["X-Title"] = "LeadSystem"
    return headers


def complete(
    system: str,
    user: str,
    *,
    temperature: float = 0.7,
    max_tokens: int = 800,
    timeout: float = 60.0,
) -> str:
    """Send a system+user prompt to the configured open model and return text.

    Raises LLMUnavailable if the endpoint cannot be reached or errors.
    """
    settings = get_settings()
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=_headers())
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("LLM call failed (%s): %s", settings.llm_provider, exc)
        raise LLMUnavailable(str(exc)) from exc


def health() -> dict:
    """Lightweight probe used by the dashboard to show AI status."""
    settings = get_settings()
    info = {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "ok": False,
        "detail": "",
    }
    try:
        text = complete("You are a health check.", "Reply with the single word: OK",
                        temperature=0.0, max_tokens=5, timeout=8.0)
        info["ok"] = True
        info["detail"] = text[:60]
    except LLMUnavailable as exc:
        info["detail"] = str(exc)[:160]
    return info
