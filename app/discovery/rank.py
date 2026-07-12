"""Dedupe + rank raw creator dicts into a shortlist of the top few."""
from __future__ import annotations


def rank_creators(raw: list[dict], top_n: int = 10) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    for c in raw:
        handle = (c.get("handle") or "").strip().lower()
        if not handle:
            continue
        key = (c.get("platform", ""), handle)
        prev = best.get(key)
        if prev is None or c.get("followers", 0) > prev.get("followers", 0):
            best[key] = c
    ranked = sorted(
        best.values(),
        key=lambda c: (c.get("followers", 0), c.get("engagement_score", 0.0)),
        reverse=True,
    )
    return ranked[:top_n]
