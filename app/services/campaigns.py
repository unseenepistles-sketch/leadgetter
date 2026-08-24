"""Email campaigns to opted-in leads.

Drafting uses the open-source LLM (template fallback). Sending goes only to
SUBSCRIBED leads, and every message carries a working unsubscribe link — the
baseline CAN-SPAM / GDPR requirements for permission-based email. If SMTP is
not configured, messages are rendered and logged (dry-run) instead of sent.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai import llm
from ..config import get_settings
from ..models import Campaign, EmailEvent, Lead
from . import leads as leads_svc

log = logging.getLogger("leadsystem.campaigns")

SYSTEM = (
    "You are an email copywriter for permission-based marketing. Write a single "
    "campaign email to people who opted in. Return it as:\n"
    "SUBJECT: <one line>\n\n<body>\n"
    "Keep it friendly, valuable, and honest. 120-200 words. One clear call to "
    "action. Do not invent facts about the reader."
)


def draft(niche: str, offer: str, goal: str = "") -> tuple[str, str]:
    """Return (subject, body). LLM-written when available, else templated."""
    user = (
        f"Audience niche: {niche or 'general'}\n"
        f"What I'm offering / promoting: {offer}\n"
        f"Goal of this email: {goal or 'drive clicks to the offer'}\n"
        "Write the email."
    )
    try:
        text = llm.complete(SYSTEM, user, temperature=0.8, max_tokens=500)
        return _split_subject(text, offer)
    except llm.LLMUnavailable:
        return _template(niche, offer)


def _split_subject(text: str, offer: str) -> tuple[str, str]:
    subject = f"A quick note about {offer}"[:120]
    body = text
    for line in text.splitlines():
        if line.strip().lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip() or subject
            body = text.split(line, 1)[1].strip()
            break
    return subject, body


def _template(niche: str, offer: str) -> tuple[str, str]:
    subject = f"Something for the {niche} community" if niche else "A little something for you"
    body = (
        f"Hi there,\n\nThanks for signing up — I don't take that lightly.\n\n"
        f"I wanted to share {offer} with you. I built it for people like you, and I "
        f"think you'll get real value from it.\n\nTake a look when you have a minute, "
        f"and just hit reply if you have any questions — a real person (me) reads every "
        f"response.\n\nTalk soon."
    )
    return subject, body


def create(db: Session, *, name: str, niche: str, subject: str, body: str) -> Campaign:
    campaign = Campaign(name=name, niche=niche, subject=subject, body=body, status="draft")
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


def send(db: Session, campaign: Campaign) -> dict:
    """Send to all subscribed leads in the campaign's niche (or all if blank)."""
    settings = get_settings()
    recipients = leads_svc.subscribed_leads(db, campaign.niche or None)
    sent = failed = 0
    live = settings.email_live
    server = _smtp_connect() if live else None
    try:
        for lead in recipients:
            body = _personalise(campaign.body, lead, settings.app_base_url)
            ok, detail = (_deliver(server, settings, lead, campaign.subject, body)
                          if live else (True, "dry-run (SMTP not configured)"))
            db.add(EmailEvent(
                campaign_id=campaign.id, lead_id=lead.id,
                status="sent" if ok else "failed", detail=detail,
            ))
            sent += int(ok)
            failed += int(not ok)
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass

    campaign.status = "sent"
    campaign.sent_at = datetime.now(timezone.utc)
    db.commit()
    return {"recipients": len(recipients), "sent": sent, "failed": failed, "live": live}


def _personalise(body: str, lead: Lead, base_url: str) -> str:
    unsub = f"{base_url}/unsubscribe/{lead.unsubscribe_token}"
    greeting = f"Hi {lead.name}," if lead.name else "Hi,"
    if body.lstrip().lower().startswith("hi"):
        greeting = ""
    footer = (
        f"\n\n—\nYou're receiving this because you opted in at {base_url}.\n"
        f"Unsubscribe anytime: {unsub}"
    )
    return f"{greeting}\n\n{body}{footer}" if greeting else f"{body}{footer}"


def _smtp_connect():
    settings = get_settings()
    server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    server.starttls()
    server.login(settings.smtp_user, settings.smtp_password)
    return server


def _deliver(server, settings, lead: Lead, subject: str, body: str) -> tuple[bool, str]:
    try:
        msg = EmailMessage()
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from}>"
        msg["To"] = lead.email
        msg["Subject"] = subject
        msg["List-Unsubscribe"] = f"<{settings.app_base_url}/unsubscribe/{lead.unsubscribe_token}>"
        msg.set_content(body)
        server.send_message(msg)
        return True, "sent"
    except Exception as exc:  # noqa: BLE001
        log.warning("Send to %s failed: %s", lead.email, exc)
        return False, str(exc)[:200]


def stats(db: Session) -> dict:
    total = db.execute(select(func.count(Campaign.id))).scalar_one()
    sent_events = db.execute(
        select(func.count(EmailEvent.id)).where(EmailEvent.status == "sent")
    ).scalar_one()
    return {"campaigns": total, "emails_sent": sent_events}
