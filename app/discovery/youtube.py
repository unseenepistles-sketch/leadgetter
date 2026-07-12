"""Creator discovery via the official YouTube Data API v3.

Uses only public channel data (title, url, subscriber count). Free quota is
~10k units/day; a channel search costs 100 units. Returns [] when no key is set.
"""
from __future__ import annotations

import httpx

from ..config import get_settings

settings = get_settings()
_SEARCH = "https://www.googleapis.com/youtube/v3/search"
_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"


def search_channels(term: str, region_code: str | None = None, max_results: int = 5) -> list[dict]:
    if not settings.youtube_enabled:
        return []
    try:
        with httpx.Client(timeout=20) as client:
            params = {
                "key": settings.youtube_api_key,
                "part": "snippet",
                "type": "channel",
                "q": term,
                "maxResults": max(1, min(max_results, 25)),
            }
            if region_code:
                params["regionCode"] = region_code
            r = client.get(_SEARCH, params=params)
            r.raise_for_status()
            items = r.json().get("items", [])
            channel_ids = [
                it["snippet"]["channelId"]
                for it in items
                if it.get("snippet", {}).get("channelId")
            ]
            if not channel_ids:
                return []
            r2 = client.get(
                _CHANNELS,
                params={
                    "key": settings.youtube_api_key,
                    "part": "snippet,statistics",
                    "id": ",".join(channel_ids),
                },
            )
            r2.raise_for_status()
            return [_to_creator(c) for c in r2.json().get("items", [])]
    except Exception:
        return []


def _to_creator(ch: dict) -> dict:
    snip = ch.get("snippet", {})
    stats = ch.get("statistics", {})
    subs = int(stats.get("subscriberCount", 0) or 0)
    views = int(stats.get("viewCount", 0) or 0)
    videos = int(stats.get("videoCount", 0) or 0)
    avg_views = round(views / videos) if videos else 0
    handle = snip.get("customUrl") or snip.get("title", "")
    return {
        "platform": "youtube",
        "handle": handle,
        "display_name": snip.get("title"),
        "url": f"https://www.youtube.com/channel/{ch.get('id', '')}",
        "followers": subs,
        "engagement_score": float(avg_views),
        "public_contact": None,
        "description": (snip.get("description") or "")[:500],
    }
