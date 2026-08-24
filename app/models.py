"""Database models.

Design note on compliance:
  * `Creator` holds PUBLIC business/creator data (handle, public contact the
    creator chose to publish). This is B2B outreach data.
  * `Lead` holds a person's contact details ONLY when they gave them to us
    themselves through the opt-in form. `consent` + `consent_ts` record that.
    There is no table for scraped personal emails/phones, by design.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Creator(Base):
    """A public creator discovered in a niche — a partnership target."""

    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("platform", "handle", name="uq_platform_handle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))          # youtube | instagram | tiktok
    handle: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    followers: Mapped[int] = mapped_column(Integer, default=0)
    niche: Mapped[str] = mapped_column(String(255), default="", index=True)
    location: Mapped[str] = mapped_column(String(255), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    # Only populated when the creator PUBLISHED a business contact email.
    public_email: Mapped[str] = mapped_column(String(255), default="")

    outreach_status: Mapped[str] = mapped_column(String(32), default="new")  # new|drafted|sent|replied
    outreach_message: Mapped[str] = mapped_column(Text, default="")

    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Lead(Base):
    """A person who opted in through our own form. Consent is mandatory."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    niche: Mapped[str] = mapped_column(String(255), default="", index=True)
    # Free-text note of where they came from (e.g. "creator:@handle" or "landing").
    source: Mapped[str] = mapped_column(String(255), default="landing")

    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_ip: Mapped[str] = mapped_column(String(64), default="")

    status: Mapped[str] = mapped_column(String(32), default="subscribed")  # subscribed|unsubscribed
    unsubscribe_token: Mapped[str] = mapped_column(String(64), default=lambda: secrets.token_urlsafe(24))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Campaign(Base):
    """An email campaign sent only to opted-in, subscribed leads."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    niche: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|sent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list["EmailEvent"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")


class EmailEvent(Base):
    """One row per (campaign, lead) send, plus open/click tracking."""

    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    status: Mapped[str] = mapped_column(String(32), default="sent")  # sent|opened|clicked|failed
    detail: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    campaign: Mapped["Campaign"] = relationship(back_populates="events")
