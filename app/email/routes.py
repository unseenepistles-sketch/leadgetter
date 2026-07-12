"""Email campaign routes: AI-draft a campaign, create it in Listmonk, send it.

Campaigns only ever go to your opted-in Listmonk list, and every body carries an
unsubscribe link ({{ UnsubscribeURL }}), which Listmonk renders per-recipient.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..ai import claude
from ..config import get_settings
from ..db import get_session
from ..email import listmonk
from ..models import Campaign
from ..web import templates

router = APIRouter()
settings = get_settings()


@router.get("/campaigns")
def campaigns(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(Campaign).order_by(Campaign.created_at.desc())).all()
    return templates.TemplateResponse(
        request,
        "campaigns.html",
        {
            "request": request,
            "campaigns": rows,
            "listmonk_enabled": settings.listmonk_enabled,
            "brand": settings.brand_name,
            "product": settings.product,
        },
    )


@router.post("/campaigns")
def create(
    name: str = Form(...),
    niche_name: str = Form(""),
    goal: str = Form("Invite them to learn more and take the next step."),
    session: Session = Depends(get_session),
):
    subject, body = claude.draft_campaign(
        niche_name or settings.product, settings.product, settings.brand_name, goal
    )
    campaign = Campaign(name=name, subject=subject, body=body, status="draft")
    lm_id = listmonk.create_campaign(name, subject, body, settings.listmonk_list_id)
    if lm_id:
        campaign.listmonk_campaign_id = lm_id
    session.add(campaign)
    session.commit()
    return RedirectResponse("/campaigns", status_code=303)


@router.post("/campaigns/{campaign_id}/send")
def send(campaign_id: int, session: Session = Depends(get_session)):
    campaign = session.get(Campaign, campaign_id)
    if campaign and campaign.listmonk_campaign_id:
        if listmonk.start_campaign(campaign.listmonk_campaign_id):
            campaign.status = "running"
            session.add(campaign)
            session.commit()
    return RedirectResponse("/campaigns", status_code=303)
