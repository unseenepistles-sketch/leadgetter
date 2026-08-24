"""Optional Google Sheets mirror for leads and creators.

Enabled only when SHEETS_ENABLED=true and a service-account JSON is present.
All functions are best-effort and safe to call when disabled — they no-op.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from ..config import get_settings

log = logging.getLogger("leadsystem.sheets")

LEADS_HEADER = ["email", "name", "niche", "source", "status", "consent_ts", "created_at"]
CREATORS_HEADER = ["platform", "handle", "name", "followers", "niche", "public_email", "url"]


@lru_cache
def _client():
    settings = get_settings()
    if not (settings.sheets_enabled and settings.sheets_spreadsheet_id):
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(
            settings.google_service_account_json, scopes=scopes
        )
        gc = gspread.authorize(creds)
        return gc.open_by_key(settings.sheets_spreadsheet_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Google Sheets unavailable: %s", exc)
        return None


def _worksheet(title: str, header: list[str]):
    book = _client()
    if book is None:
        return None
    try:
        ws = book.worksheet(title)
    except Exception:  # noqa: BLE001 - not found -> create
        ws = book.add_worksheet(title=title, rows=1000, cols=max(10, len(header)))
        ws.append_row(header)
    return ws


def upsert_lead(lead) -> None:
    ws = _worksheet("Leads", LEADS_HEADER)
    if ws is None:
        return
    row = [
        lead.email, lead.name, lead.niche, lead.source, lead.status,
        lead.consent_ts.isoformat() if lead.consent_ts else "",
        lead.created_at.isoformat() if lead.created_at else "",
    ]
    try:
        cell = ws.find(lead.email)
        if cell:
            ws.update(f"A{cell.row}:G{cell.row}", [row])
            return
    except Exception:  # noqa: BLE001
        pass
    ws.append_row(row)


def append_creators(creators) -> None:
    ws = _worksheet("Creators", CREATORS_HEADER)
    if ws is None:
        return
    rows = [[
        c.platform, c.handle, c.name, c.followers, c.niche, c.public_email, c.url
    ] for c in creators]
    if rows:
        ws.append_rows(rows)
