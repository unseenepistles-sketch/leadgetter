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

        # --- our own bookkeeping, deliberately NOT in their workbook ---
        self.ledger_path = os.getenv("LEDGER_PATH", "./data/reminders.sqlite3")

        # --- policy ---
        self.due_soon_days = _int(os.getenv("DUE_SOON_DAYS"), 180)
        self.overdue_grace_days = _int(os.getenv("OVERDUE_GRACE_DAYS"), 30)
        self.first_issue_grace_days = _int(os.getenv("FIRST_ISSUE_GRACE_DAYS"), 30)

        # --- reminders ---
        self.reminders_enabled = _bool(os.getenv("REMINDERS_ENABLED"), False)
        self.reminders_dry_run = _bool(os.getenv("REMINDERS_DRY_RUN"), True)
        self.reminder_hour = _int(os.getenv("REMINDER_HOUR"), 7)
        #: Nothing whose due date precedes this ever sends — the go-live guard.
        self.reminders_start_date = os.getenv("REMINDERS_START_DATE", "")
        #: Hard ceiling per run. Stops a misconfiguration mailing the whole company.
        self.max_sends_per_run = _int(os.getenv("MAX_SENDS_PER_RUN"), 200)
        #: Outside production every recipient is rewritten to these addresses.
        self.recipient_allowlist = [
            a.strip() for a in os.getenv("RECIPIENT_ALLOWLIST", "").split(",") if a.strip()
        ]
        self.stores_email = os.getenv("STORES_EMAIL", "")

        # --- smtp ---
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = _int(os.getenv("SMTP_PORT"), 25)
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", "uniforms@example.com")
        self.smtp_starttls = _bool(os.getenv("SMTP_STARTTLS"), True)

        self.brand_name = os.getenv("BRAND_NAME", "Uniform Manager")

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host)

    @property
    def sending_for_real(self) -> bool:
        return self.reminders_enabled and not self.reminders_dry_run and self.smtp_enabled

    def capabilities(self) -> dict[str, bool]:
        return {
            "Workbook": Path(self.workbook_path).exists(),
            "SMTP configured": self.smtp_enabled,
            "Reminders enabled": self.reminders_enabled,
            "Live sending": self.sending_for_real,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
