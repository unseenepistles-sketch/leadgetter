"""Opt-in lead capture and management.

A Lead is only ever created from a person's own submission on our form, with an
explicit consent checkbox. We record consent + timestamp + IP so the list is
provably permission-based and legal to email.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Lead
from . import sheets

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def capture(db: Session, *, email: str, name: str = "", niche: str = "",
            source: str = "landing", consent: bool, ip: str = "") -> tuple[Lead | None, str]:
    """Create or reactivate a lead. Returns (lead, message).

    Consent is mandatory — without it we refuse to store the contact.
    """
    email = (email or "").strip().lower()
    if not valid_email(email):
        return None, "Please enter a valid email address."
    if not consent:
        return None, "We can only add you if you tick the consent box."

    existing = db.execute(select(Lead).where(Lead.email == email)).scalar_one_or_none()
    if existing:
        # Re-subscribe if they'd previously left; never silently duplicate.
        existing.status = "subscribed"
        existing.consent = True
        existing.consent_ts = datetime.now(timezone.utc)
        existing.consent_ip = ip or existing.consent_ip
        if name:
            existing.name = name
        db.commit()
        db.refresh(existing)
        _sync(existing)
        return existing, "You're already on the list — welcome back!"

    lead = Lead(
        email=email,
        name=name,
        niche=niche,
        source=source,
        consent=True,
        consent_ts=datetime.now(timezone.utc),
        consent_ip=ip,
        status="subscribed",
        unsubscribe_token=secrets.token_urlsafe(24),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    _sync(lead)
    return lead, "Thanks — you're in. Check your inbox."


def unsubscribe(db: Session, token: str) -> bool:
    lead = db.execute(select(Lead).where(Lead.unsubscribe_token == token)).scalar_one_or_none()
    if not lead:
        return False
    lead.status = "unsubscribed"
    db.commit()
    _sync(lead)
    return True


def subscribed_leads(db: Session, niche: str | None = None) -> list[Lead]:
    stmt = select(Lead).where(Lead.status == "subscribed")
    if niche:
        stmt = stmt.where(Lead.niche == niche)
    return list(db.execute(stmt.order_by(Lead.created_at.desc())).scalars())


def counts(db: Session) -> dict:
    total = db.execute(select(func.count(Lead.id))).scalar_one()
    subbed = db.execute(
        select(func.count(Lead.id)).where(Lead.status == "subscribed")
    ).scalar_one()
    return {"total": total, "subscribed": subbed, "unsubscribed": total - subbed}


def _sync(lead: Lead) -> None:
    """Best-effort mirror to Google Sheets; never breaks the request."""
    try:
        sheets.upsert_lead(lead)
    except Exception:  # noqa: BLE001
        pass
