"""Background jobs.

The workbook is polled for outside edits; nothing else runs on a timer. The
reminder mailer that used to live here was removed: alerts in this system are
visual, on the renewal and "still to come" screens, and an app that can email
the whole workforce is a liability when nobody wants it to.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import get_settings
from .deps import get_store

log = logging.getLogger("uniforms.jobs")

_scheduler: BackgroundScheduler | None = None


def reload_workbook_if_changed() -> None:
    """Notice when someone has edited the workbook in Excel."""
    try:
        if get_store().reload_if_changed():
            log.info("workbook changed on disk; reloaded")
    except Exception:
        log.exception("workbook poll failed")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    settings = get_settings()
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        reload_workbook_if_changed, "interval",
        seconds=settings.reload_poll_seconds, id="workbook-poll",
        max_instances=1, coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    log.info("scheduler started (workbook poll every %ss)", settings.reload_poll_seconds)
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
