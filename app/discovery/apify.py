"""Public IG/TikTok creator discovery via Apify actors.

Only public profile data (handle, url, follower count, and a public business
email if the profile publishes one) is read. Never follower lists or private
data. Returns [] when no token is set or on any error (best-effort by design).
"""
from __future__ import annotations

from ..config import get_settings

settings = get_settings()

_INSTAGRAM_ACTOR = "apify/instagram-scraper"
_TIKTOK_ACTOR = "clockworks/tiktok-scraper"


def _run_actor(actor_id: str, run_input: dict) -> list[dict]:
    if not settings.apify_enabled:
        return []
    try:
        from apify_client import ApifyClient

        client = ApifyClient(settings.apify_token)
        run = client.actor(actor_id).call(run_input=run_input, timeout_secs=120)
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            return []
        return list(client.dataset(dataset_id).iterate_items())
    except Exception:
        return []


def search_instagram(term: str, max_results: int = 5) -> list[dict]:
    items = _run_actor(
        _INSTAGRAM_ACTOR,
        {"search": term, "searchType": "user", "searchLimit": max_results,
         "resultsType": "details", "resultsLimit": max_results},
    )
    out: list[dict] = []
    for it in items[:max_results]:
        username = _first(it, "username", "ownerUsername", "handle")
        if not username:
            continue
        out.append({
            "platform": "instagram",
            "handle": username,
            "display_name": _first(it, "fullName", "name") or username,
            "url": f"https://www.instagram.com/{username}/",
            "followers": _int(_first(it, "followersCount", "followers")),
            "engagement_score": float(_int(_first(it, "postsCount", "mediaCount"))),
            "public_contact": _first(it, "businessEmail", "publicEmail", "email"),
            "description": (_first(it, "biography", "bio") or "")[:500],
        })
    return out


def search_tiktok(term: str, max_results: int = 5) -> list[dict]:
    items = _run_actor(
        _TIKTOK_ACTOR,
        {"searchQueries": [term], "resultsPerPage": max_results,
         "shouldDownloadVideos": False, "shouldDownloadCovers": False},
    )
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        author = it.get("authorMeta") or it
        username = _first(author, "name", "uniqueId", "nickName")
        if not username or username in seen:
            continue
        seen.add(username)
        out.append({
            "platform": "tiktok",
            "handle": username,
            "display_name": _first(author, "nickName", "nickname") or username,
            "url": f"https://www.tiktok.com/@{username}",
            "followers": _int(_first(author, "fans", "followers", "followerCount")),
            "engagement_score": float(_int(_first(author, "heart", "heartCount", "likes"))),
            "public_contact": None,
            "description": (_first(author, "signature", "bio") or "")[:500],
        })
        if len(out) >= max_results:
            break
    return out


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
