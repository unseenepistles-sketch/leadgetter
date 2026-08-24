"""Draft partnership outreach to CREATORS (not their followers).

This is B2B outreach: proposing a shoutout / collaboration / affiliate deal to
a public creator, using the AI layer to personalise. If the LLM is unreachable
it falls back to a solid template so you always get a usable draft.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..ai import llm
from ..models import Creator

SYSTEM = (
    "You write short, warm, high-converting partnership outreach messages from a "
    "creator/brand to another creator. You propose a shoutout, collaboration, or "
    "affiliate deal. Keep it under 90 words, specific, respectful of their time, "
    "with one clear call to action. No hype, no emojis spam, no fake flattery."
)


def draft_message(creator: Creator, offer: str, sender_name: str = "") -> str:
    """Return an outreach message for a creator, LLM-written or templated."""
    followers = f"{creator.followers:,}" if creator.followers else "your"
    user = (
        f"Creator: {creator.name} (@{creator.handle}) on {creator.platform}, "
        f"{followers} followers, niche: {creator.niche or 'n/a'}.\n"
        f"Their bio: {creator.bio or 'n/a'}\n"
        f"My offer to promote: {offer}\n"
        f"My name/brand: {sender_name or 'us'}\n"
        "Write the outreach message."
    )
    try:
        return llm.complete(SYSTEM, user, temperature=0.8, max_tokens=300)
    except llm.LLMUnavailable:
        return _template(creator, offer, sender_name)


def _template(creator: Creator, offer: str, sender_name: str) -> str:
    who = creator.name or f"@{creator.handle}"
    signoff = sender_name or "—"
    return (
        f"Hi {who}, I've been following your {creator.niche or creator.platform} "
        f"content and love how you connect with your audience. I run {offer}, and I "
        f"think it'd genuinely resonate with the people who follow you.\n\n"
        f"Would you be open to a paid shoutout or an affiliate collaboration? Happy to "
        f"send a free sample and a commission structure that makes it worth your while.\n\n"
        f"Either way, keep up the great work.\n{signoff}"
    )


def draft_and_store(db: Session, creator: Creator, offer: str, sender_name: str = "") -> Creator:
    creator.outreach_message = draft_message(creator, offer, sender_name)
    creator.outreach_status = "drafted"
    db.commit()
    db.refresh(creator)
    return creator
