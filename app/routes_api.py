"""JSON API that backs the single-page console UI.

Thin endpoints over the real services — discovery, OpenRouter-drafted outreach,
consent-first opt-in + welcome email, campaigns. The browser UI (templates/
console.html) calls these with fetch(); nothing here is mock.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import state
from .config import get_settings
from .database import get_session
from .models import Campaign, Creator, EmailEvent, Lead
from .services import ads, campaigns, creators, leads, outreach, sheets

router = APIRouter(prefix="/api")


# ── request bodies ─────────────────────────────────────────────────────────
class SettingsIn(BaseModel):
    offer: str = ""
    sender: str = ""
    magnet: str = ""


class DiscoverIn(BaseModel):
    niche: str
    location: str = ""
    platform: str = "instagram"
    limit: int = 12


class SubscribeIn(BaseModel):
    email: str
    name: str = ""
    consent: bool = False


class CampaignIn(BaseModel):
    name: str
    goal: str = ""


# ── serialisers ────────────────────────────────────────────────────────────
def _creator(c: Creator) -> dict:
    return {"id": c.id, "name": c.name or c.handle, "handle": c.handle,
            "platform": c.platform, "followers": c.followers,
            "drafted": c.outreach_status == "drafted", "message": c.outreach_message}


def _lead(l: Lead) -> dict:
    return {"id": l.id, "email": l.email, "name": l.name, "source": l.source, "status": l.status}


def _campaign(cp: Campaign) -> dict:
    return {"id": cp.id, "name": cp.name, "subject": cp.subject, "body": cp.body,
            "status": cp.status, "recipients": len(cp.events)}


def _ts(dt: datetime | None) -> int:
    if not dt:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _activity(db: Session) -> list[dict]:
    """Derive a recent-activity feed from stored data — newest first."""
    items: list[dict] = []
    for l in db.execute(select(Lead).order_by(Lead.created_at.desc()).limit(15)).scalars():
        items.append({"txt": f"New opt-in: <b>{l.email}</b> (consent logged)",
                      "kind": "green", "ts": _ts(l.created_at)})
    for ev in db.execute(
        select(EmailEvent).order_by(EmailEvent.created_at.desc()).limit(15)
    ).scalars():
        lead = db.get(Lead, ev.lead_id)
        who = lead.email if lead else f"lead #{ev.lead_id}"
        label = "Welcome email" if ev.detail.startswith("welcome") else "Campaign email"
        items.append({"txt": f"{label} sent to <b>{who}</b>", "kind": "blue", "ts": _ts(ev.created_at)})
    for cp in db.execute(
        select(Campaign).where(Campaign.status != "auto").order_by(Campaign.created_at.desc()).limit(8)
    ).scalars():
        items.append({"txt": f"Drafted campaign <b>{cp.name}</b>", "kind": "green", "ts": _ts(cp.created_at)})
    latest = db.execute(select(Creator).order_by(Creator.discovered_at.desc()).limit(1)).scalar_one_or_none()
    if latest:
        n = db.query(Creator).count()
        items.append({"txt": f"Discovered <b>{n}</b> creators", "kind": "green", "ts": _ts(latest.discovered_at)})
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:40]


def _bootstrap(db: Session) -> dict:
    settings = get_settings()
    st = state.load()
    creator_rows = list(db.execute(select(Creator).order_by(Creator.followers.desc()).limit(50)).scalars())
    lead_rows = list(db.execute(select(Lead).order_by(Lead.created_at.desc()).limit(200)).scalars())
    campaign_rows = list(db.execute(
        select(Campaign).where(Campaign.status != "auto").order_by(Campaign.created_at.desc()).limit(20)
    ).scalars())
    counts = leads.counts(db)
    cstats = campaigns.stats(db)
    return {
        "settings": {"offer": st.get("offer", ""), "sender": st.get("sender_name", ""),
                     "magnet": st.get("lead_magnet_url", "")},
        "status": {
            "ai": {"live": settings.ai_configured, "provider": settings.llm_provider, "model": settings.llm_model},
            "discovery": settings.discovery_live, "email": settings.email_live, "sheets": settings.sheets_enabled,
        },
        "stats": {"creators": len(creator_rows), "leads": counts["subscribed"],
                  "emails": cstats["emails_sent"], "campaigns": cstats["campaigns"]},
        "creators": [_creator(c) for c in creator_rows],
        "leads": [_lead(l) for l in lead_rows],
        "campaigns": [_campaign(cp) for cp in campaign_rows],
        "keywords": ads.interest_keywords(db, st.get("niche") or None),
        "activity": _activity(db),
    }


# ── endpoints ──────────────────────────────────────────────────────────────
@router.get("/bootstrap")
def bootstrap(db: Session = Depends(get_session)):
    return _bootstrap(db)


@router.post("/settings")
def save_settings(body: SettingsIn, db: Session = Depends(get_session)):
    state.save(offer=body.offer, sender_name=body.sender, lead_magnet_url=body.magnet)
    return _bootstrap(db)


@router.post("/discover")
def discover(body: DiscoverIn, db: Session = Depends(get_session)):
    state.save(niche=body.niche, location=body.location, platform=body.platform)
    found, source = creators.discover(body.niche, body.location, body.platform, body.limit)
    saved = creators.save_creators(db, found, body.niche)
    try:
        sheets.append_creators(saved)
    except Exception:  # noqa: BLE001
        pass
    data = _bootstrap(db)
    data["flash"] = {"source": source, "count": len(saved)}
    return data


@router.post("/outreach/{creator_id}")
def make_outreach(creator_id: int, db: Session = Depends(get_session)):
    creator = db.get(Creator, creator_id)
    if creator:
        st = state.load()
        outreach.draft_and_store(db, creator, st.get("offer") or "my product", st.get("sender_name", ""))
    return _bootstrap(db)


@router.post("/subscribe")
def subscribe(body: SubscribeIn, request: Request, db: Session = Depends(get_session)):
    st = state.load()
    fwd = request.headers.get("x-forwarded-for")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")
    lead, message, created = leads.capture(
        db, email=body.email, name=body.name, niche=st.get("niche", ""),
        source="console", consent=body.consent, ip=ip,
    )
    if lead is not None and created:
        try:
            campaigns.send_welcome(db, lead, st.get("offer", ""),
                                   st.get("lead_magnet_url", ""), st.get("welcome_body", ""))
        except Exception:  # noqa: BLE001
            pass
    data = _bootstrap(db)
    data["flash"] = {"ok": lead is not None, "message": message}
    return data


@router.post("/campaigns")
def draft_campaign(body: CampaignIn, db: Session = Depends(get_session)):
    st = state.load()
    subject, cbody = campaigns.draft(st.get("niche", ""), st.get("offer") or "my offer", body.goal)
    campaigns.create(db, name=body.name, niche=st.get("niche", ""), subject=subject, body=cbody)
    return _bootstrap(db)


@router.post("/campaigns/{campaign_id}/send")
def send_campaign(campaign_id: int, db: Session = Depends(get_session)):
    campaign = db.get(Campaign, campaign_id)
    result = {"sent": 0, "recipients": 0, "live": get_settings().email_live}
    if campaign and campaign.status != "sent":
        result = campaigns.send(db, campaign)
    data = _bootstrap(db)
    data["flash"] = result
    return data


@router.post("/leads/{lead_id}/toggle")
def toggle_lead(lead_id: int, db: Session = Depends(get_session)):
    lead = db.get(Lead, lead_id)
    if lead:
        lead.status = "unsubscribed" if lead.status == "subscribed" else "subscribed"
        db.commit()
    return _bootstrap(db)
