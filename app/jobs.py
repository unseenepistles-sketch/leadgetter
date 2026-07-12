"""Background jobs: mirror new leads to Sheets, refresh campaign stats."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from .db import engine
from .email import listmonk
from .models import Campaign
from .store import sheets

log = logging.getLogger("leadsystem.jobs")
_scheduler: BackgroundScheduler | None = None


def _sync_sheets() -> None:
    try:
        with Session(engine) as session:
            n = sheets.sync_unsynced(session)
            if n:
                log.info("Synced %s leads to Google Sheets", n)
    except Exception:
        log.exception("sheet sync failed")


def _refresh_stats() -> None:
    try:
        with Session(engine) as session:
            rows = session.exec(
                select(Campaign).where(Campaign.listmonk_campaign_id.is_not(None))
            ).all()
            for c in rows:
                stats = listmonk.campaign_stats(c.listmonk_campaign_id)
                if stats:
                    c.sent, c.opens, c.clicks = stats["sent"], stats["opens"], stats["clicks"]
                    if stats.get("status"):
                        c.status = stats["status"]
                    session.add(c)
            session.commit()
    except Exception:
        log.exception("stats refresh failed")


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(_sync_sheets, "interval", minutes=10, id="sync_sheets")
    _scheduler.add_job(_refresh_stats, "interval", minutes=15, id="refresh_stats")
    _scheduler.start()


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
