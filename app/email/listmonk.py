"""Thin client for the self-hosted Listmonk HTTP API.

Listmonk owns list management, double opt-in, unsubscribe, and bounce handling,
so those compliance essentials are handled for us. All calls are best-effort:
if Listmonk is not configured/reachable they return None and the app continues.
"""
from __future__ import annotations

import httpx

from ..config import get_settings

settings = get_settings()


def _client() -> httpx.Client | None:
    if not settings.listmonk_enabled:
        return None
    return httpx.Client(
        base_url=settings.listmonk_url,
        auth=(settings.listmonk_user, settings.listmonk_password),
        timeout=20,
    )


def upsert_subscriber(email: str, name: str | None, list_id: int) -> int | None:
    """Create/confirm a subscriber on a list. Leaves them 'unconfirmed' so a
    double-opt-in list sends the confirmation email (we never preconfirm)."""
    client = _client()
    if client is None:
        return None
    try:
        with client:
            payload = {
                "email": email,
                "name": name or email.split("@")[0],
                "status": "enabled",
                "lists": [list_id] if list_id else [],
                "preconfirm_subscriptions": False,
            }
            r = client.post("/api/subscribers", json=payload)
            if r.status_code in (200, 201):
                return r.json().get("data", {}).get("id")
            if r.status_code == 409:  # already exists -> look up + attach to list
                return _attach_existing(client, email, list_id)
            return None
    except Exception:
        return None


def _attach_existing(client: httpx.Client, email: str, list_id: int) -> int | None:
    try:
        r = client.get("/api/subscribers", params={"query": f"email = '{email}'"})
        results = r.json().get("data", {}).get("results", [])
        if not results:
            return None
        sub_id = results[0].get("id")
        if sub_id and list_id:
            client.put(
                "/api/subscribers/lists",
                json={"ids": [sub_id], "action": "add", "target_list_ids": [list_id]},
            )
        return sub_id
    except Exception:
        return None


def create_campaign(name: str, subject: str, body: str, list_id: int) -> int | None:
    client = _client()
    if client is None:
        return None
    try:
        with client:
            r = client.post(
                "/api/campaigns",
                json={
                    "name": name,
                    "subject": subject,
                    "lists": [list_id] if list_id else [],
                    "type": "regular",
                    "content_type": "html",
                    "body": body,
                },
            )
            if r.status_code in (200, 201):
                return r.json().get("data", {}).get("id")
            return None
    except Exception:
        return None


def start_campaign(campaign_id: int) -> bool:
    client = _client()
    if client is None:
        return False
    try:
        with client:
            r = client.put(
                f"/api/campaigns/{campaign_id}/status", json={"status": "running"}
            )
            return r.status_code in (200, 201)
    except Exception:
        return False


def campaign_stats(campaign_id: int) -> dict | None:
    """Return {sent, opens, clicks} for a campaign, or None on error."""
    client = _client()
    if client is None:
        return None
    try:
        with client:
            r = client.get(f"/api/campaigns/{campaign_id}")
            if r.status_code != 200:
                return None
            d = r.json().get("data", {})
            return {
                "sent": int(d.get("sent", 0) or 0),
                "opens": int(d.get("views", d.get("opens", 0)) or 0),
                "clicks": int(d.get("clicks", 0) or 0),
                "status": d.get("status", ""),
            }
    except Exception:
        return None
