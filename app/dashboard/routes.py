"""Dashboard: the control center. Metric tiles, charts, and a discovery form."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from ..config import get_settings
from ..db import get_session
from ..models import Campaign, Creator, Lead, Niche
from ..web import templates

router = APIRouter()
settings = get_settings()


@router.get("/")
def dashboard(request: Request, session: Session = Depends(get_session)):
    leads = session.exec(select(Lead)).all()
    creators = session.exec(select(Creator)).all()
    campaigns = session.exec(select(Campaign)).all()
    niches = session.exec(select(Niche)).all()

    recent_leads = sorted(leads, key=lambda x: x.created_at, reverse=True)[:8]
    top_creators = sorted(creators, key=lambda x: x.followers, reverse=True)[:8]

    counts = {
        "leads": len(leads),
        "subscribed": sum(1 for x in leads if x.status == "subscribed"),
        "creators": len(creators),
        "campaigns": len(campaigns),
        "niches": len(niches),
    }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "counts": counts,
            "capabilities": settings.capabilities(),
            "recent_leads": recent_leads,
            "top_creators": top_creators,
            "brand": settings.brand_name,
        },
    )


@router.get("/api/metrics")
def metrics(session: Session = Depends(get_session)) -> dict:
    leads = session.exec(select(Lead)).all()
    creators = session.exec(select(Creator)).all()
    campaigns = session.exec(select(Campaign)).all()

    # leads captured per day, last 14 days
    days = [(date.today() - timedelta(days=i)) for i in range(13, -1, -1)]
    per_day = Counter(x.created_at.date() for x in leads if x.created_at)
    leads_by_day = {
        "labels": [d.strftime("%b %d") for d in days],
        "values": [per_day.get(d, 0) for d in days],
    }

    by_platform = Counter(c.platform for c in creators)
    creators_by_platform = {
        "labels": list(by_platform.keys()),
        "values": list(by_platform.values()),
    }

    campaign_totals = {
        "sent": sum(c.sent for c in campaigns),
        "opens": sum(c.opens for c in campaigns),
        "clicks": sum(c.clicks for c in campaigns),
    }
    return {
        "leads_by_day": leads_by_day,
        "creators_by_platform": creators_by_platform,
        "campaign_totals": campaign_totals,
    }


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
