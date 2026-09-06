"""Runtime configuration, read from the environment with a .env fallback.

Every integration degrades gracefully: if its keys are unset the feature reports
itself as disabled and the rest of the app keeps working.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

try:  # optional: load a local .env for convenience
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class Settings:
    def __init__(self) -> None:
        # --- the workbook: their database ---
        self.workbook_path = os.getenv("WORKBOOK_PATH", "./data/uniforms.xlsx")
        self.backup_dir = os.getenv("BACKUP_DIR", "") or str(
            Path(self.workbook_path).parent / "backups"
        )
        self.max_backups = _int(os.getenv("MAX_BACKUPS"), 10)
        #: 03/04/2024 is 3 April when true, 4 March when false. Must match their locale.
        self.dates_day_first = _bool(os.getenv("DATES_DAY_FIRST"), True)
        self.flush_debounce_seconds = float(os.getenv("FLUSH_DEBOUNCE_SECONDS", "2"))
        #: How often to notice that someone edited the workbook by hand.
        self.reload_poll_seconds = _int(os.getenv("RELOAD_POLL_SECONDS"), 60)

        # --- policy ---
        self.due_soon_days = _int(os.getenv("DUE_SOON_DAYS"), 180)
        self.overdue_grace_days = _int(os.getenv("OVERDUE_GRACE_DAYS"), 30)
        self.first_issue_grace_days = _int(os.getenv("FIRST_ISSUE_GRACE_DAYS"), 30)
        #: How long a promised but undelivered garment may sit before it needs chasing.
        self.chase_after_days = _int(os.getenv("CHASE_AFTER_DAYS"), 30)

        self.brand_name = os.getenv("BRAND_NAME", "Uniform Manager")

        # --- sign-in ---
        # Off by default so a local demo needs no setup. Switch on before this is
        # reachable by anyone but you.
        self.auth_enabled = _bool(os.getenv("AUTH_ENABLED"), False)
        self.auth_users = os.getenv("AUTH_USERS", "")
        self.secret_key = os.getenv("SECRET_KEY", "")

    def capabilities(self) -> dict[str, bool]:
        return {
            "Workbook": Path(self.workbook_path).exists(),
            "Sign-in required": self.auth_enabled and bool(self.auth_users),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
