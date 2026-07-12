"""AI layer (Anthropic).

- Niche-term expansion + light creator classification run on a cheap model
  (Claude Haiku 4.5) since they are high-volume and simple.
- Outreach + email copy run on a stronger model (Claude Sonnet 5) for quality.

Every function has a deterministic fallback so the app works with no API key.
"""
from __future__ import annotations

import json
import re

from ..config import get_settings

settings = get_settings()


def _client():
    """Return an Anthropic client, or None if unavailable."""
    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic
    except Exception:
        return None
    try:
        return anthropic.Anthropic(api_key=settings.anthropic_api_key)
    except Exception:
        return None


def _complete(model: str, prompt: str, max_tokens: int = 1024) -> str | None:
    client = _client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Niche-term expansion (cheap model)
# --------------------------------------------------------------------------- #
def expand_search_terms(seed: str, location: str | None = None, n: int = 8) -> list[str]:
    """Turn one seed phrase into several concrete creator-search phrases."""
    loc = f" The audience is focused on: {location}." if location else ""
    prompt = (
        f"I am finding top social-media creators in a niche to research and "
        f'partner with. Seed topic: "{seed}".{loc}\n'
        f"Return up to {n} short search phrases (2-4 words each) a person would "
        f"type to find leading creators/channels in this niche. Include close "
        f"synonyms and adjacent sub-topics"
        + (", and append the location to a few of them" if location else "")
        + ".\nReturn ONLY a JSON array of strings, no prose."
    )
    text = _complete(settings.haiku_model, prompt, max_tokens=400)
    terms = _parse_string_list(text) if text else []
    if not terms:
        terms = _fallback_terms(seed, location, n)
    # always keep the raw seed in the mix, dedupe, cap
    out: list[str] = []
    for t in [seed, *terms]:
        t = t.strip()
        if t and t.lower() not in {o.lower() for o in out}:
            out.append(t)
    return out[:n]


def _fallback_terms(seed: str, location: str | None, n: int) -> list[str]:
    words = [w for w in re.split(r"[\s,]+", seed) if w]
    terms: list[str] = [seed]
    terms += words  # each keyword on its own
    if location:
        terms += [f"{seed} {location}"] + [f"{w} {location}" for w in words]
    seen: list[str] = []
    for t in terms:
        if t and t.lower() not in {s.lower() for s in seen}:
            seen.append(t)
    return seen[:n]


# --------------------------------------------------------------------------- #
# Copywriting (stronger model)
# --------------------------------------------------------------------------- #
def draft_outreach(creator_name: str, platform: str, product: str, brand: str) -> str:
    prompt = (
        f"Write a short, friendly partnership outreach message from {brand} to "
        f"{creator_name}, a creator on {platform}. We sell: {product}. Propose a "
        f"collaboration/shoutout/affiliate that would genuinely help their "
        f"audience. 4-6 sentences, warm and specific, no hype, include a clear "
        f"low-pressure ask. Return only the message text."
    )
    text = _complete(settings.copy_model, prompt, max_tokens=500)
    if text:
        return text.strip()
    return (
        f"Hi {creator_name}, I'm with {brand}. I love what you're building for "
        f"your {platform} audience. We offer {product}, and I think a "
        f"collaboration (a shoutout, guest content, or an affiliate deal) could "
        f"be a real win for your community. Would you be open to a quick chat?"
    )


def draft_campaign(niche_name: str, product: str, brand: str, goal: str) -> tuple[str, str]:
    """Return (subject, html_body) for an opted-in email campaign."""
    prompt = (
        f"Write a marketing email for {brand} promoting: {product}. Audience: "
        f'people who opted in around "{niche_name}". Goal: {goal}. Keep it warm '
        f"and concise. Requirements: one compelling subject line, then the email "
        f"body as simple HTML paragraphs. IMPORTANT: end the body with an "
        f'unsubscribe line using the literal token {{{{ UnsubscribeURL }}}}.\n'
        f'Return JSON: {{"subject": "...", "body_html": "..."}}'
    )
    text = _complete(settings.copy_model, prompt, max_tokens=900)
    if text:
        data = _parse_json_object(text)
        if data and data.get("subject") and data.get("body_html"):
            body = data["body_html"]
            if "UnsubscribeURL" not in body:
                body += '\n<p><a href="{{ UnsubscribeURL }}">Unsubscribe</a></p>'
            return data["subject"].strip(), body
    subject = f"{product} — a quick note from {brand}"
    body = (
        f"<p>Hi {{{{ .Subscriber.FirstName }}}},</p>"
        f"<p>Thanks for your interest in {niche_name}. I wanted to share "
        f"{product} with you.</p><p>{goal}</p>"
        f"<p>Warmly,<br>{brand}</p>"
        f'<p><a href="{{{{ UnsubscribeURL }}}}">Unsubscribe</a></p>'
    )
    return subject, body


# --------------------------------------------------------------------------- #
# parsing helpers
# --------------------------------------------------------------------------- #
def _parse_string_list(text: str) -> list[str]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return [str(x) for x in data if str(x).strip()]
        except Exception:
            pass
    return [ln.strip("-*# \t") for ln in text.splitlines() if ln.strip()]


def _parse_json_object(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None
