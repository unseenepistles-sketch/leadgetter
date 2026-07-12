"""Mirror leads into a Google Sheet (Postgres/SQLite stays the source of truth).

Best-effort: if Sheets is not configured or the client library is missing,
these functions no-op and return 0 so the rest of the app is unaffected.
"""
from __future__ import annotations

from sqlmodel import Session, select

from ..config import get_settings
from ..models import Lead

settings = get_settings()
_HEADER = ["id", "email", "name", "source", "niche_id", "consent_at", "status", "created_at"]


def _worksheet():
    if not settings.sheets_enabled:
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(settings.google_sa_json, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(settings.sheet_id)
        try:
            ws = sh.worksheet("Leads")
        except Exception:
            ws = sh.add_worksheet(title="Leads", rows=1000, cols=len(_HEADER))
        if ws.row_count == 0 or ws.acell("A1").value != "id":
            ws.update("A1", [_HEADER])
        return ws
    except Exception:
        return None


def _row(lead: Lead) -> list:
    return [
        lead.id, lead.email, lead.name or "", lead.source, lead.niche_id or "",
        lead.consent_at.isoformat() if lead.consent_at else "",
        lead.status,
        lead.created_at.isoformat() if lead.created_at else "",
    ]


def append_lead(lead: Lead) -> bool:
    ws = _worksheet()
    if ws is None:
        return False
    try:
        ws.append_row(_row(lead), value_input_option="RAW")
        return True
    except Exception:
        return False


def sync_unsynced(session: Session) -> int:
    """Push all not-yet-synced leads and mark them synced. Returns count."""
    ws = _worksheet()
    if ws is None:
        return 0
    leads = session.exec(select(Lead).where(Lead.synced_to_sheet == False)).all()  # noqa: E712
    written = 0
    for lead in leads:
        try:
            ws.append_row(_row(lead), value_input_option="RAW")
            lead.synced_to_sheet = True
            session.add(lead)
            written += 1
        except Exception:
            break
    if written:
        session.commit()
    return written
