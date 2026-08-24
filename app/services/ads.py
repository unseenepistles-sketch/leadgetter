"""Ad-audience export — the compliant, scalable way to reach a creator's followers.

You cannot lawfully scrape a creator's followers and cold-message them. But every
major ad platform is *built* to let you target the people who follow or engage
with specific creators/topics. This service turns the creators you discovered
into ready-to-use targeting inputs:

  * A CSV of creator handles + follower counts (paste into Meta/TikTok Ads
    Manager "detailed targeting" / audience research).
  * A de-duplicated list of interest keywords derived from the niche + creator
    bios, to seed interest-based and lookalike audiences.

This reaches the same people your instinct was pointing at — a big creator's
audience — but through the front door, at scale, without harvesting anyone's
personal data.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Creator

_STOP = {
    "the", "and", "for", "with", "your", "you", "our", "are", "this", "that",
    "top", "best", "sample", "data", "creator", "content", "voice", "connect",
    "env", "keys", "live", "results", "official", "page", "www", "com",
}


def audience_csv(db: Session, niche: str | None = None) -> str:
    """CSV of creators to use as ad-targeting seeds."""
    stmt = select(Creator).order_by(Creator.followers.desc())
    if niche:
        stmt = stmt.where(Creator.niche == niche)
    creators = list(db.execute(stmt).scalars())

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["platform", "handle", "name", "followers", "niche", "profile_url",
                "suggested_use"])
    for c in creators:
        w.writerow([
            c.platform, f"@{c.handle}", c.name, c.followers, c.niche, c.url,
            "detailed-targeting / audience-research seed",
        ])
    return buf.getvalue()


def interest_keywords(db: Session, niche: str | None = None, limit: int = 25) -> list[str]:
    """Frequency-ranked keywords from the niche + creator bios for interest targeting."""
    stmt = select(Creator)
    if niche:
        stmt = stmt.where(Creator.niche == niche)
    creators = list(db.execute(stmt).scalars())

    counter: Counter[str] = Counter()
    corpus = " ".join([niche or ""] + [c.niche for c in creators] + [c.bio for c in creators])
    for word in re.findall(r"[a-zA-Z]{3,}", corpus.lower()):
        if word in _STOP:
            continue
        counter[word] += 1
    return [w for w, _ in counter.most_common(limit)]
