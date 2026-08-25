"""Process-wide singletons: the workbook store, the service, the ledger.

The workbook is parsed once at startup and held in memory — re-reading it per
request would cost seconds at the client's volume.
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from typing import Optional

from .auth import Authenticator, build_authenticator
from .config import Settings, get_settings
from .domain.due import Policy
from .excelstore.workbook import WorkbookStore, create_blank_workbook
from .reminders.ledger import ReminderLedger
from .reminders.mailer import ConsoleMailer, Mailer, SmtpMailer
from .reminders.runner import ReminderRunner
from .service import UniformService

log = logging.getLogger("uniforms.deps")


@lru_cache
def get_store() -> WorkbookStore:
    settings = get_settings()
    store = WorkbookStore(
        settings.workbook_path,
        backup_dir=settings.backup_dir,
        dayfirst=settings.dates_day_first,
        max_backups=settings.max_backups,
        flush_debounce_seconds=settings.flush_debounce_seconds,
    )
    if not store.path.exists():
        log.warning("no workbook at %s — creating an empty one", store.path)
        create_blank_workbook(store.path)
    return store


@lru_cache
def get_service() -> UniformService:
    settings = get_settings()
    return UniformService(
        get_store(),
        Policy(
            due_soon_days=settings.due_soon_days,
            overdue_grace_days=settings.overdue_grace_days,
            first_issue_grace_days=settings.first_issue_grace_days,
        ),
    )


@lru_cache
def get_auth() -> Authenticator:
    return build_authenticator()


@lru_cache
def get_ledger() -> ReminderLedger:
    return ReminderLedger(get_settings().ledger_path)


def build_mailer(settings: Optional[Settings] = None) -> Mailer:
    settings = settings or get_settings()
    if settings.smtp_enabled:
        return SmtpMailer(
            settings.smtp_host,
            settings.smtp_port,
            sender=settings.smtp_from,
            user=settings.smtp_user,
            password=settings.smtp_password,
            starttls=settings.smtp_starttls,
        )
    return ConsoleMailer()


def get_runner() -> ReminderRunner:
    settings = get_settings()
    return ReminderRunner(
        get_service(),
        get_ledger(),
        build_mailer(settings),
        stores_email=settings.stores_email,
        allowlist=settings.recipient_allowlist,
        max_sends_per_run=settings.max_sends_per_run,
        start_date=_parse_cutoff(settings.reminders_start_date),
        brand=settings.brand_name,
    )


def _parse_cutoff(value: str) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        log.warning("REMINDERS_START_DATE %r is not an ISO date; ignoring", value)
        return None
