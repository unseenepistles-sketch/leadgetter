"""Orchestrates a discovery run: expand terms -> gather -> rank -> persist."""
from __future__ import annotations

from sqlmodel import Session, select

from ..ai import claude
from ..models import Creator, Niche, OutreachTask
from . import apify, youtube
from .rank import rank_creators

_CREATOR_FIELDS = (
    "platform", "handle", "display_name", "url",
    "followers", "engagement_score", "public_contact", "description",
)

# minimal country-name -> ISO 3166-1 alpha-2 map for YouTube regionCode
_REGIONS = {
    "kenya": "KE", "united states": "US", "usa": "US", "us": "US",
    "united kingdom": "GB", "uk": "GB", "canada": "CA", "australia": "AU",
    "nigeria": "NG", "south africa": "ZA", "india": "IN", "germany": "DE",
    "france": "FR", "brazil": "BR", "mexico": "MX", "spain": "ES",
    "ghana": "GH", "uganda": "UG", "tanzania": "TZ",
}


def region_code(location: str | None) -> str | None:
    if not location:
        return None
    return _REGIONS.get(location.strip().lower())


def run_discovery(
    session: Session,
    seed: str,
    location: str | None = None,
    platforms: tuple[str, ...] | list[str] = ("youtube",),
    per_term: int = 5,
    top_n: int = 10,
) -> dict:
    terms = claude.expand_search_terms(seed, location)
    region = region_code(location)

    raw: list[dict] = []
    for term in terms:
        if "youtube" in platforms:
            raw += youtube.search_channels(term, region, per_term)
        if "instagram" in platforms:
            raw += apify.search_instagram(term, per_term)
        if "tiktok" in platforms:
            raw += apify.search_tiktok(term, per_term)

    ranked = rank_creators(raw, top_n)

    niche = Niche(name=seed, keywords=terms, location=location or None)
    session.add(niche)
    session.commit()
    session.refresh(niche)

    saved = 0
    for c in ranked:
        existing = session.exec(
            select(Creator).where(
                Creator.platform == c["platform"],
                Creator.handle == c["handle"],
            )
        ).first()
        if existing:
            existing.niche_id = niche.id
            existing.followers = c.get("followers", existing.followers)
            existing.engagement_score = c.get("engagement_score", existing.engagement_score)
            creator = existing
        else:
            creator = Creator(niche_id=niche.id, **{k: c.get(k) for k in _CREATOR_FIELDS})
            session.add(creator)
            saved += 1
        session.commit()
        session.refresh(creator)

        has_task = session.exec(
            select(OutreachTask).where(OutreachTask.creator_id == creator.id)
        ).first()
        if not has_task:
            session.add(OutreachTask(creator_id=creator.id))
    session.commit()

    return {
        "niche_id": niche.id,
        "terms": terms,
        "found": len(ranked),
        "saved": saved,
        "region": region,
        "platforms": list(platforms),
    }
