"""Background jobs: the daily reminder run, and noticing hand edits to the workbook."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import get_settings
from .deps import get_runner, get_store

log = logging.getLogger("uniforms.jobs")
_scheduler: BackgroundScheduler | None = None


def run_reminders() -> None:
    settings = get_settings()
    if not settings.reminders_enabled:
        log.info("reminders disabled (REMINDERS_ENABLED is off); skipping")
        return
    try:
        # run_locked takes a per-day lock, so several workers cannot each send.
        result = get_runner().run_locked(dry_run=settings.reminders_dry_run)
        if result is None:
            return
        log.info("reminder run: %s", result.as_dict())
    except Exception:
        log.exception("reminder run failed")


def reload_workbook() -> None:
    """Pick up edits someone made in Excel directly."""
    try:
        if get_store().reload_if_changed():
            log.info("workbook changed on disk; reloaded")
    except Exception:
        log.exception("workbook reload failed")


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    settings = get_settings()

    get_store().start_writer()

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        run_reminders, "cron", hour=settings.reminder_hour, minute=0,
        id="reminders", max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    _scheduler.add_job(
        reload_workbook, "interval", seconds=settings.reload_poll_seconds,
        id="reload_workbook", max_instances=1, coalesce=True,
    )
    _scheduler.start()
    log.info(
        "scheduler started (reminders at %02d:00, enabled=%s, dry_run=%s)",
        settings.reminder_hour, settings.reminders_enabled, settings.reminders_dry_run,
    )


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    # Drain anything still queued so a restart never loses a recorded handover.
    get_store().stop_writer()
