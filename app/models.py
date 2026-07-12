"""Data model.

Compliance note: a `Lead` is only ever created through the public opt-in route,
and `consent_at` is mandatory — there is no code path that manufactures a lead
without a recorded consent timestamp, and none that stores follower contact data.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Text
from sqlmodel import JSON, Column, Field, SQLModel


class Niche(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    location: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Creator(SQLModel, table=True):
    """A public creator/business profile — market research + partnership target.

    Only public profile data lives here (handle, url, follower count, a public
    business contact if the profile publishes one). Never follower lists or PII.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    niche_id: Optional[int] = Field(default=None, foreign_key="niche.id")
    platform: str  # youtube | instagram | tiktok
    handle: str = Field(index=True)
    display_name: Optional[str] = None
    url: str
    followers: int = 0
    engagement_score: float = 0.0
    public_contact: Optional[str] = None  # only if the profile publishes one
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    discovered_at: datetime = Field(default_factory=datetime.utcnow)


class Lead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    name: Optional[str] = None
    source: str = "landing"
    niche_id: Optional[int] = Field(default=None, foreign_key="niche.id")
    # Mandatory: recorded at the moment the person opts in.
    consent_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "new"  # new | subscribed | unsubscribed | bounced
    listmonk_subscriber_id: Optional[int] = None
    synced_to_sheet: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Campaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    subject: str
    body: Optional[str] = Field(default=None, sa_column=Column(Text))
    listmonk_campaign_id: Optional[int] = None
    status: str = "draft"  # draft | scheduled | running | finished
    sent: int = 0
    opens: int = 0
    clicks: int = 0
    unsubscribes: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OutreachTask(SQLModel, table=True):
    """Low-volume partnership outreach to a creator's *public business contact*."""

    id: Optional[int] = Field(default=None, primary_key=True)
    creator_id: int = Field(foreign_key="creator.id")
    status: str = "to_contact"  # to_contact | contacted | replied | deal | passed
    channel: Optional[str] = None  # email | dm (sent manually by you)
    draft_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    updated_at: datetime = Field(default_factory=datetime.utcnow)
