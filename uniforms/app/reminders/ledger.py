"""The record of which reminders have already gone out.

This deliberately does **not** live in the client's workbook. It is our
operational bookkeeping rather than their data, and it needs a real uniqueness
guarantee: if someone re-sorts or deletes spreadsheet rows, the consequence must
never be re-emailing ten thousand people.

The claim is the whole mechanism. A row is inserted *before* the message is sent,
keyed on ``employee|item|due_date|stage``. A duplicate insert fails, so a restart,
a retry or two workers racing cannot produce a second email. Because the due date
is part of the key, next cycle's reminder is a different key and correctly sends
again.

Ordering the insert before the send means a crash in between causes a *missed*
email — visible in the ledger as ``pending`` and re-sent on the next run — rather
than a duplicate one. For a reminder system that trade is the right way round.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional, Sequence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminder_log (
    dedupe_key      TEXT PRIMARY KEY,
    employee_number TEXT NOT NULL,
    item_code       TEXT NOT NULL,
    due_date        TEXT,
    stage           TEXT NOT NULL,
    recipient       TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    claimed_at      TEXT,
    sent_at         TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminder_status ON reminder_log(status);
CREATE INDEX IF NOT EXISTS idx_reminder_employee ON reminder_log(employee_number);

CREATE TABLE IF NOT EXISTS job_runs (
    job_key    TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    claimed    INTEGER NOT NULL DEFAULT 0,
    sent       INTEGER NOT NULL DEFAULT 0,
    failed     INTEGER NOT NULL DEFAULT 0,
    note       TEXT
);
"""

PENDING = "pending"
SENT = "sent"
FAILED = "failed"
SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class Claim:
    dedupe_key: str
    employee_number: str
    item_code: str
    due_date: Optional[date]
    stage: str
    recipient: str


def dedupe_key(
    employee_number: str, item_code: str, due: Optional[date], stage: str, recipient: str = ""
) -> str:
    """Identity of a single reminder.

    The recipient is part of the key because one stage can have several audiences
    — the six-month notice goes to stores *and* the employee. Without it the two
    copies collide and only one is ever sent.
    """
    parts = [employee_number, item_code, due.isoformat() if due else "-", stage]
    if recipient:
        parts.append(recipient)
    return "|".join(parts)


class ReminderLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ claims

    def claim(self, claim: Claim, *, status: str = PENDING) -> bool:
        """Reserve a reminder. False means someone already has it."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO reminder_log
                    (dedupe_key, employee_number, item_code, due_date, stage,
                     recipient, status, claimed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim.dedupe_key,
                    claim.employee_number,
                    claim.item_code,
                    claim.due_date.isoformat() if claim.due_date else None,
                    claim.stage,
                    claim.recipient,
                    status,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return cur.rowcount == 1

    def already_handled(self, key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM reminder_log WHERE dedupe_key = ?", (key,)
            ).fetchone()
        return row is not None and row["status"] in (SENT, SUPPRESSED)

    def pending_among(self, keys: Sequence[str]) -> set[str]:
        """Which of these keys are claimed but not yet sent.

        A crashed or failed run leaves rows pending; the next run must retry them
        rather than treating the existing claim as proof the email went out.
        """
        if not keys:
            return set()
        found: set[str] = set()
        with self._connect() as conn:
            for chunk in (keys[i : i + 500] for i in range(0, len(keys), 500)):
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT dedupe_key FROM reminder_log "
                    f"WHERE status = ? AND dedupe_key IN ({placeholders})",
                    (PENDING, *chunk),
                ).fetchall()
                found.update(r["dedupe_key"] for r in rows)
        return found

    def pending(self, limit: int = 1000) -> list[sqlite3.Row]:
        """Includes stragglers from earlier runs, not just today's claims."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM reminder_log WHERE status = ? ORDER BY claimed_at LIMIT ?",
                (PENDING, limit),
            ).fetchall()

    def mark_sent(self, keys: Sequence[str]) -> None:
        if not keys:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.executemany(
                "UPDATE reminder_log SET status=?, sent_at=?, error=NULL WHERE dedupe_key=?",
                [(SENT, now, k) for k in keys],
            )

    def mark_failed(self, keys: Sequence[str], error: str, *, max_attempts: int = 3) -> None:
        """Retry a few times, then quarantine rather than looping forever."""
        if not keys:
            return
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                UPDATE reminder_log
                   SET attempts = attempts + 1,
                       error = ?,
                       status = CASE WHEN attempts + 1 >= ? THEN ? ELSE ? END
                 WHERE dedupe_key = ?
                """,
                [(error[:500], max_attempts, FAILED, PENDING, k) for k in keys],
            )

    def suppress(self, claims: Sequence[Claim]) -> int:
        """Pre-claim reminders as suppressed — the go-live backfill guard."""
        return sum(1 for c in claims if self.claim(c, status=SUPPRESSED))

    def history(self, employee_number: Optional[str] = None, limit: int = 200) -> list[sqlite3.Row]:
        with self._connect() as conn:
            if employee_number:
                return conn.execute(
                    "SELECT * FROM reminder_log WHERE employee_number=? "
                    "ORDER BY COALESCE(sent_at, claimed_at) DESC LIMIT ?",
                    (employee_number, limit),
                ).fetchall()
            return conn.execute(
                "SELECT * FROM reminder_log ORDER BY COALESCE(sent_at, claimed_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM reminder_log GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # --------------------------------------------------------------- job locks

    def start_run(self, job: str, day: date) -> bool:
        """Claim today's run. False means another worker already has it.

        ``BackgroundScheduler`` lives in-process, so four uvicorn workers would
        otherwise mean four schedulers and four sets of emails.
        """
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO job_runs (job_key, started_at) VALUES (?, ?)",
                (f"{job}|{day.isoformat()}", datetime.now().isoformat(timespec="seconds")),
            )
            return cur.rowcount == 1

    def finish_run(
        self, job: str, day: date, *, claimed: int, sent: int, failed: int, note: str = ""
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE job_runs SET finished_at=?, claimed=?, sent=?, failed=?, note=?
                 WHERE job_key=?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    claimed, sent, failed, note, f"{job}|{day.isoformat()}",
                ),
            )

    def last_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM job_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
