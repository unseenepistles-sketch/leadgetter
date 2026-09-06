"""Process-wide singletons: the workbook store and the service.

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
