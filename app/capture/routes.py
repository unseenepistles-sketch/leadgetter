"""Public lead-magnet landing page + consent-gated opt-in.

This is the ONLY place a Lead is created, and consent is mandatory: a submission
without the consent checkbox is rejected. `consent_at` is stamped server-side.
"""
from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from sqlmodel import Session

from ..config import get_settings
from ..db import get_session
from ..email import listmonk
from ..models import Lead
from ..store import sheets
from ..web import templates

router = APIRouter()
settings = get_settings()
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/l")
@router.get("/l/{niche_id}")
def landing(request: Request, niche_id: int | None = None):
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "request": request,
            "niche_id": niche_id,
            "brand": settings.brand_name,
            "product": settings.product,
            "lead_magnet": settings.lead_magnet,
            "error": None,
        },
    )


@router.post("/subscribe")
def subscribe(
    request: Request,
    email: str = Form(...),
    name: str = Form(""),
    niche_id: int | None = Form(None),
    consent: str = Form(""),
    session: Session = Depends(get_session),
):
    # Guardrail 1: explicit consent is required.
    if consent.lower() not in ("on", "true", "yes", "1"):
        return _reject(request, niche_id, "Please tick the consent box to continue.")
    # Guardrail 2: valid email.
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return _reject(request, niche_id, "Please enter a valid email address.")

    lead = Lead(
        email=email,
        name=name.strip() or None,
        source="landing",
        niche_id=niche_id,
        consent_at=datetime.utcnow(),  # recorded consent timestamp
        status="new",
    )
    session.add(lead)
    session.commit()
    session.refresh(lead)

    # Best-effort: double opt-in via Listmonk, then mirror to the sheet.
    sub_id = listmonk.upsert_subscriber(lead.email, lead.name, settings.listmonk_list_id)
    if sub_id:
        lead.listmonk_subscriber_id = sub_id
        lead.status = "subscribed"
    if sheets.append_lead(lead):
        lead.synced_to_sheet = True
    session.add(lead)
    session.commit()

    return templates.TemplateResponse(
        request,
        "thankyou.html",
        {"request": request, "brand": settings.brand_name, "lead_magnet": settings.lead_magnet},
    )


def _reject(request: Request, niche_id: int | None, error: str):
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "request": request,
            "niche_id": niche_id,
            "brand": settings.brand_name,
            "product": settings.product,
            "lead_magnet": settings.lead_magnet,
            "error": error,
        },
        status_code=400,
    )
