"""Creator discovery.

Given a niche (search terms + optional location), find the top public creators
on a platform, ranked by following. Uses an Apify Store actor when APIFY_TOKEN
is set; otherwise returns realistic sample creators so the whole app is usable
offline.

IMPORTANT — what this does and does NOT collect:
  * It collects PUBLIC creator profiles: handle, name, follower count, bio, and
    a public business email ONLY if the creator published one. That is B2B
    partnership data.
  * It does NOT, and will not, enumerate a creator's followers or collect the
    personal emails/phone numbers of individuals. Those aren't exposed by the
    platforms and harvesting them for cold outreach is unlawful (TCPA/CAN-SPAM/
    GDPR) and a ban risk. The compliant way to reach a creator's audience is
    creator partnerships (see outreach.py) and interest-based ads
    (see ads.py) — both built here.
"""
from __future__ import annotations

import hashlib
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Creator

log = logging.getLogger("leadsystem.creators")

PLATFORMS = ("instagram", "youtube", "tiktok")


# ── Public discovery API ──────────────────────────────────────────────────

def discover(niche: str, location: str = "", platform: str = "instagram",
             limit: int = 25) -> tuple[list[dict], str]:
    """Return (creators, source) where source is 'apify' or 'sample'."""
    settings = get_settings()
    niche = (niche or "").strip()
    platform = (platform or "instagram").strip().lower()
    if platform not in PLATFORMS:
        platform = "instagram"

    if settings.discovery_live:
        try:
            creators = _discover_apify(niche, location, platform, limit)
            if creators:
                return creators, "apify"
            log.info("Apify returned no items; falling back to sample data.")
        except Exception as exc:  # noqa: BLE001 - never let discovery hard-fail the request
            log.warning("Apify discovery failed: %s", exc)

    return _sample_creators(niche, location, platform, limit), "sample"


def save_creators(db: Session, creators: list[dict], niche: str) -> list[Creator]:
    """Persist creators, de-duplicating on (platform, handle)."""
    saved: list[Creator] = []
    for c in creators:
        handle = (c.get("handle") or "").lstrip("@").strip()
        platform = (c.get("platform") or "").lower()
        if not handle or not platform:
            continue
        existing = db.execute(
            select(Creator).where(Creator.platform == platform, Creator.handle == handle)
        ).scalar_one_or_none()
        if existing:
            # Refresh volatile fields.
            existing.followers = c.get("followers", existing.followers)
            existing.niche = niche or existing.niche
            saved.append(existing)
            continue
        creator = Creator(
            platform=platform,
            handle=handle,
            name=c.get("name", ""),
            url=c.get("url", ""),
            followers=int(c.get("followers", 0) or 0),
            niche=niche,
            location=c.get("location", ""),
            bio=c.get("bio", ""),
            public_email=c.get("public_email", ""),
        )
        db.add(creator)
        saved.append(creator)
    db.commit()
    for creator in saved:
        db.refresh(creator)
    return saved


# ── Apify integration ─────────────────────────────────────────────────────

def _discover_apify(niche: str, location: str, platform: str, limit: int) -> list[dict]:
    """Run the configured Apify actor synchronously and map its output.

    Actor output schemas vary, so we map defensively across common field
    names. Override the actor via APIFY_CREATOR_ACTOR for your platform.
    """
    settings = get_settings()
    actor = settings.apify_creator_actor.replace("/", "~")  # Apify path form
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
    search = f"{niche} {location}".strip()
    payload = {
        "search": search,
        "searchType": "user",
        "resultsLimit": limit,
        "maxItems": limit,
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, params={"token": settings.apify_token}, json=payload)
        resp.raise_for_status()
        items = resp.json()

    creators: list[dict] = []
    for it in items[:limit]:
        handle = it.get("username") or it.get("handle") or it.get("channelName") or ""
        if not handle:
            continue
        creators.append({
            "platform": platform,
            "handle": handle,
            "name": it.get("fullName") or it.get("name") or it.get("channelName") or handle,
            "url": it.get("url") or it.get("channelUrl") or "",
            "followers": int(
                it.get("followersCount") or it.get("followers")
                or it.get("subscriberCount") or it.get("subscribers") or 0
            ),
            "location": location,
            "bio": it.get("biography") or it.get("description") or "",
            # Some business profiles expose a public contact email.
            "public_email": it.get("publicEmail") or it.get("email") or "",
        })
    creators.sort(key=lambda c: c["followers"], reverse=True)
    return creators


# ── Sample data (offline / no token) ──────────────────────────────────────

def _sample_creators(niche: str, location: str, platform: str, limit: int) -> list[dict]:
    """Deterministic, realistic-looking creators derived from the niche.

    Clearly synthetic (handles are prefixed) so nobody mistakes them for live
    data. Lets you click through the entire workflow with no API keys.
    """
    seed = niche.lower() or "creators"
    tokens = [t for t in seed.replace(",", " ").split() if t] or ["niche"]
    domain = f"{platform}.com"
    out: list[dict] = []
    n = max(1, min(limit, 25))
    for i in range(n):
        base = tokens[i % len(tokens)]
        h = hashlib.sha1(f"{seed}-{platform}-{i}".encode()).hexdigest()
        num = int(h[:6], 16)
        handle = f"sample_{base}{num % 1000}"
        followers = 2_000_000 // (i + 1) + (num % 5000)  # descending, ranked
        loc = f" · {location}" if location else ""
        out.append({
            "platform": platform,
            "handle": handle,
            "name": f"{base.title()} Creator {i + 1}",
            "url": f"https://{domain}/{handle}",
            "followers": followers,
            "location": location,
            "bio": f"[SAMPLE DATA] Top {base} voice{loc}. Connect env keys for live results.",
            "public_email": "",  # samples never fabricate contact info
        })
    out.sort(key=lambda c: c["followers"], reverse=True)
    return out
