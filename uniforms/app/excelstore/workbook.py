"""The Excel workbook as the system of record.

The client's ``.xlsx`` *is* the database — nothing is migrated anywhere. But at
10,000+ employees that file holds hundreds of thousands of rows, and openpyxl needs
seconds and a lot of memory to parse it, so re-reading it per web request is not an
option.

The arrangement:

* **Read once, index in memory.** Every query is served from the index. The file on
  disk stays the truth; we simply stop re-parsing it constantly.
* **One writer.** Mutations update the index immediately (so the user sees their
  change at once) and enqueue a row. A single background thread drains the queue and
  persists. Concurrent requests can never interleave writes and corrupt the file.
* **Never write in place.** Save to a temporary file, then atomically replace, so a
  crash mid-write cannot leave a half-written workbook.
* **Back up before every flush**, keeping the most recent few.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from openpyxl import Workbook, load_workbook

from ..domain.models import (
    Employee,
    EmployeeStatus,
    Entitlement,
    Issuance,
    UniformItem,
)
from . import fast_append, schema
from .parsing import (
    cell,
    is_blank_row,
    map_headers,
    normalise_header,
    parse_bool,
    parse_date,
    parse_int,
    parse_text,
)

log = logging.getLogger("uniforms.workbook")


class WorkbookError(RuntimeError):
    """The workbook could not be read or written."""


class WorkbookLocked(WorkbookError):
    """Someone has the file open in Excel, or the OS is holding it."""


@dataclass(frozen=True, slots=True)
class SheetProblem:
    sheet: str
    row: Optional[int]
    message: str


@dataclass
class Snapshot:
    """An immutable-by-convention view of the workbook, indexed for reads."""

    employees: dict[str, Employee] = field(default_factory=dict)
    items: dict[str, UniformItem] = field(default_factory=dict)
    entitlements: dict[str, list[Entitlement]] = field(default_factory=dict)
    issuances: dict[str, list[Issuance]] = field(default_factory=dict)
    problems: list[SheetProblem] = field(default_factory=list)
    loaded_at: Optional[datetime] = None
    source_mtime: float = 0.0

    def entitlements_for(self, role: Optional[str]) -> list[Entitlement]:
        if not role:
            return list(self.entitlements.get("*", ()))
        key = normalise_header(role)
        return list(self.entitlements.get(key) or self.entitlements.get("*", ()))

    def issuances_for(self, employee_number: str) -> list[Issuance]:
        return list(self.issuances.get(employee_number, ()))

    @property
    def counts(self) -> dict[str, int]:
        return {
            "employees": len(self.employees),
            "items": len(self.items),
            "entitlement_rules": sum(len(v) for v in self.entitlements.values()),
            "issuances": sum(len(v) for v in self.issuances.values()),
            "problems": len(self.problems),
        }


class WorkbookStore:
    def __init__(
        self,
        path: str | Path,
        *,
        backup_dir: str | Path | None = None,
        dayfirst: bool = True,
        max_backups: int = 10,
        flush_debounce_seconds: float = 2.0,
    ) -> None:
        self.path = Path(path)
        self.backup_dir = Path(backup_dir) if backup_dir else self.path.parent / "backups"
        self.dayfirst = dayfirst
        self.max_backups = max_backups
        self.flush_debounce_seconds = flush_debounce_seconds

        self._lock = threading.RLock()
        self._snapshot = Snapshot()
        self._pending: list[Issuance] = []
        self._flush_thread: Optional[threading.Thread] = None
        self._flush_requested = threading.Event()
        self._stopping = threading.Event()
        self._last_written_mtime = 0.0
        self.last_write_error: Optional[str] = None

    # ------------------------------------------------------------------ reads

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    @property
    def pending_writes(self) -> int:
        with self._lock:
            return len(self._pending)

    def load(self) -> Snapshot:
        """Parse the whole workbook into a fresh index."""
        if not self.path.exists():
            raise WorkbookError(f"workbook not found: {self.path}")

        started = time.monotonic()
        mtime = self.path.stat().st_mtime
        try:
            wb = load_workbook(self.path, read_only=True, data_only=True)
        except Exception as exc:  # openpyxl raises a wide variety here
            raise WorkbookError(f"could not open {self.path.name}: {exc}") from exc

        try:
            snap = self._parse(wb)
        finally:
            wb.close()

        snap.loaded_at = datetime.now()
        snap.source_mtime = mtime
        with self._lock:
            self._snapshot = snap
        log.info(
            "loaded %s in %.2fs: %s",
            self.path.name,
            time.monotonic() - started,
            snap.counts,
        )
        return snap

    def reload_if_changed(self) -> bool:
        """Re-read when someone has edited the file behind our back."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        with self._lock:
            known = self._snapshot.source_mtime
            # Our own flush moved the mtime; no need to re-parse for that.
            if mtime in (known, self._last_written_mtime):
                return False
        self.load()
        return True

    # ----------------------------------------------------------------- writes

    def append_issuance(self, issuance: Issuance) -> Issuance:
        """Record a handover: visible immediately, on disk shortly after."""
        with self._lock:
            self._snapshot.issuances.setdefault(issuance.employee_number, []).append(issuance)
            self._pending.append(issuance)
        self.request_flush()
        return issuance

    def append_issuances(self, issuances: Sequence[Issuance]) -> int:
        with self._lock:
            for iss in issuances:
                self._snapshot.issuances.setdefault(iss.employee_number, []).append(iss)
            self._pending.extend(issuances)
        self.request_flush()
        return len(issuances)

    def request_flush(self) -> None:
        self._flush_requested.set()

    def start_writer(self) -> None:
        if self._flush_thread is not None:
            return
        self._stopping.clear()
        self._flush_thread = threading.Thread(
            target=self._writer_loop, name="workbook-writer", daemon=True
        )
        self._flush_thread.start()

    def stop_writer(self, *, final_flush: bool = True) -> None:
        self._stopping.set()
        self._flush_requested.set()
        thread, self._flush_thread = self._flush_thread, None
        if thread is not None:
            thread.join(timeout=60)
        if final_flush and self.pending_writes:
            try:
                self.flush()
            except WorkbookError:
                log.exception("final flush failed; queued rows remain unwritten")

    def _writer_loop(self) -> None:
        while not self._stopping.is_set():
            if not self._flush_requested.wait(timeout=1.0):
                continue
            self._flush_requested.clear()
            # Debounce so a burst of submissions costs one rewrite, not twenty.
            deadline = time.monotonic() + self.flush_debounce_seconds
            while time.monotonic() < deadline and not self._stopping.is_set():
                time.sleep(0.1)
                if self._flush_requested.is_set():
                    self._flush_requested.clear()
                    deadline = time.monotonic() + self.flush_debounce_seconds
            try:
                self.flush()
            except WorkbookError as exc:
                # Keep the rows queued and try again — never drop a write.
                self.last_write_error = str(exc)
                log.error("flush failed, %s rows still queued: %s", self.pending_writes, exc)
                time.sleep(5)
                self._flush_requested.set()
            except Exception:
                log.exception("unexpected error in workbook writer")
                time.sleep(5)

    def flush(self) -> int:
        """Persist queued rows. Returns how many were written."""
        with self._lock:
            if not self._pending:
                return 0
            batch = list(self._pending)

        self._backup()
        try:
            written = self._flush_fast(batch)
            if not written:
                self._flush_openpyxl(batch)
        except WorkbookError:
            raise
        except PermissionError as exc:
            raise WorkbookLocked(
                f"{self.path.name} is locked — it is probably open in Excel"
            ) from exc
        except Exception as exc:
            raise WorkbookError(f"could not write {self.path.name}: {exc}") from exc

        with self._lock:
            del self._pending[: len(batch)]
            self._last_written_mtime = self.path.stat().st_mtime
            self._snapshot.source_mtime = self._last_written_mtime
            self.last_write_error = None
        log.info("wrote %s issuance row(s) to %s", len(batch), self.path.name)
        return len(batch)

    def _flush_fast(self, batch: Sequence[Issuance]) -> int:
        """Splice rows straight into the sheet XML. Returns 0 if not applicable."""
        try:
            sheet_title, order = self._issuance_layout()
        except WorkbookError:
            raise
        except Exception as exc:
            log.info("fast append unavailable (%s); using openpyxl", exc)
            return 0
        if sheet_title is None:
            return 0
        try:
            return fast_append.append_rows(
                self.path, sheet_title, [_issuance_row(i, order) for i in batch]
            )
        except PermissionError:
            raise
        except fast_append.FastAppendUnsupported as exc:
            log.info("fast append not supported for this workbook (%s)", exc)
            return 0

    def _issuance_layout(self) -> tuple[Optional[str], list[str]]:
        """The issuance sheet's real title and its column order, read cheaply."""
        wb = load_workbook(self.path, read_only=True)
        try:
            ws = _find_sheet(wb, schema.ISSUANCES_SHEET)
            if ws is None:
                return None, []
            return ws.title, _append_order(ws, create_if_missing=False)
        finally:
            wb.close()

    def _flush_openpyxl(self, batch: Sequence[Issuance]) -> None:
        """The dependable path: full load, append, atomic replace."""
        tmp = self.path.with_name(f".{self.path.stem}.writing.xlsx")
        try:
            wb = load_workbook(self.path)
            ws = _find_sheet(wb, schema.ISSUANCES_SHEET)
            if ws is None:
                ws = wb.create_sheet(schema.ISSUANCES_SHEET)
                ws.append(list(schema.ISSUANCE_HEADERS))
            order = _append_order(ws)
            for iss in batch:
                ws.append(_issuance_row(iss, order))
            wb.save(tmp)
            wb.close()
            os.replace(tmp, self.path)
        except Exception:
            _quiet_unlink(tmp)
            raise

    def _backup(self) -> None:
        if not self.path.exists():
            return
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(self.path, self.backup_dir / f"{self.path.stem}-{stamp}.xlsx")
            backups = sorted(self.backup_dir.glob(f"{self.path.stem}-*.xlsx"))
            for stale in backups[: -self.max_backups]:
                _quiet_unlink(stale)
        except OSError:
            # A failed backup must not block the write it was protecting.
            log.warning("could not back up %s", self.path.name, exc_info=True)

    # ----------------------------------------------------------------- parsing

    def _parse(self, wb: Workbook) -> Snapshot:
        snap = Snapshot()
        self._parse_employees(wb, snap)
        self._parse_items(wb, snap)
        self._parse_entitlements(wb, snap)
        self._parse_issuances(wb, snap)
        return snap

    def _rows(
        self, wb: Workbook, sheet: str, columns, snap: Snapshot
    ) -> Iterable[tuple[int, Sequence[Any], dict[str, int]]]:
        ws = _find_sheet(wb, sheet)
        if ws is None:
            snap.problems.append(SheetProblem(sheet, None, "sheet not found"))
            return
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            snap.problems.append(SheetProblem(sheet, None, "sheet is empty"))
            return
        mapping, missing = map_headers(header, columns)
        if missing:
            snap.problems.append(
                SheetProblem(sheet, 1, f"missing required column(s): {', '.join(missing)}")
            )
            return
        for number, row in enumerate(rows, start=2):
            if is_blank_row(row):
                continue
            yield number, row, mapping

    def _parse_employees(self, wb: Workbook, snap: Snapshot) -> None:
        for number, row, mapping in self._rows(wb, schema.EMPLOYEES_SHEET, schema.EMPLOYEE_COLUMNS, snap):
            emp_no = parse_text(cell(row, mapping, "employee_number"))
            if not emp_no:
                snap.problems.append(SheetProblem(schema.EMPLOYEES_SHEET, number, "no employee number"))
                continue
            if emp_no in snap.employees:
                snap.problems.append(
                    SheetProblem(schema.EMPLOYEES_SHEET, number, f"duplicate employee number {emp_no}")
                )
                continue

            problems: list[str] = []
            raw_join = cell(row, mapping, "join_date")
            join = parse_date(raw_join, dayfirst=self.dayfirst)
            if join is None and raw_join not in (None, ""):
                problems.append(f"unreadable join date {raw_join!r}")
            elif join is None:
                # Reported so the client can fix it, but not attached to the
                # employee: someone with a full issuance history is still
                # judgeable without a join date, and should not be forced into
                # needs_review.
                snap.problems.append(
                    SheetProblem(
                        schema.EMPLOYEES_SHEET, number,
                        f"{emp_no}: no join date — items never issued cannot be dated",
                    )
                )

            sizes = {}
            for field_name, key in schema.SIZE_FIELDS.items():
                value = parse_text(cell(row, mapping, field_name))
                if value:
                    sizes[key] = value

            snap.employees[emp_no] = Employee(
                employee_number=emp_no,
                full_name=parse_text(cell(row, mapping, "full_name")) or emp_no,
                email=parse_text(cell(row, mapping, "email")),
                manager_email=parse_text(cell(row, mapping, "manager_email")),
                department=parse_text(cell(row, mapping, "department")),
                role=parse_text(cell(row, mapping, "role")),
                join_date=join,
                status=_employee_status(cell(row, mapping, "status")),
                sizes=sizes,
                problems=tuple(problems),
                row=number,
            )
            for message in problems:
                snap.problems.append(SheetProblem(schema.EMPLOYEES_SHEET, number, message))

    def _parse_items(self, wb: Workbook, snap: Snapshot) -> None:
        for number, row, mapping in self._rows(wb, schema.ITEMS_SHEET, schema.ITEM_COLUMNS, snap):
            code = parse_text(cell(row, mapping, "item_code"))
            if not code:
                continue
            cycle = parse_int(cell(row, mapping, "renewal_cycle_months"), 12)
            if not cycle or cycle <= 0:
                snap.problems.append(
                    SheetProblem(schema.ITEMS_SHEET, number, f"{code}: cycle must be positive, defaulting to 12")
                )
                cycle = 12
            snap.items[code] = UniformItem(
                item_code=code,
                name=parse_text(cell(row, mapping, "name")) or code,
                category=parse_text(cell(row, mapping, "category")),
                renewal_cycle_months=cycle,
                default_quantity=parse_int(cell(row, mapping, "default_quantity"), 1) or 1,
                size_key=_size_key(cell(row, mapping, "size_key"), cell(row, mapping, "category")),
                active=parse_bool(cell(row, mapping, "active"), True),
            )

    def _parse_entitlements(self, wb: Workbook, snap: Snapshot) -> None:
        grouped: dict[str, list[Entitlement]] = defaultdict(list)
        for number, row, mapping in self._rows(
            wb, schema.ENTITLEMENTS_SHEET, schema.ENTITLEMENT_COLUMNS, snap
        ):
            role = parse_text(cell(row, mapping, "role"))
            code = parse_text(cell(row, mapping, "item_code"))
            if not role or not code:
                continue
            if code not in snap.items:
                snap.problems.append(
                    SheetProblem(schema.ENTITLEMENTS_SHEET, number, f"unknown item code {code!r}")
                )
                continue
            key = "*" if role.strip() in {"*", "all", "All", "ALL"} else normalise_header(role)
            grouped[key].append(
                Entitlement(role=role, item_code=code, quantity=parse_int(cell(row, mapping, "quantity"), 1) or 1)
            )
        snap.entitlements = dict(grouped)

    def _parse_issuances(self, wb: Workbook, snap: Snapshot) -> None:
        grouped: dict[str, list[Issuance]] = defaultdict(list)
        for number, row, mapping in self._rows(
            wb, schema.ISSUANCES_SHEET, schema.ISSUANCE_COLUMNS, snap
        ):
            emp_no = parse_text(cell(row, mapping, "employee_number"))
            code = parse_text(cell(row, mapping, "item_code"))
            if not emp_no or not code:
                snap.problems.append(
                    SheetProblem(schema.ISSUANCES_SHEET, number, "missing employee number or item code")
                )
                continue
            issued = parse_date(cell(row, mapping, "issued_date"), dayfirst=self.dayfirst)
            if issued is None:
                snap.problems.append(
                    SheetProblem(
                        schema.ISSUANCES_SHEET,
                        number,
                        f"unreadable issue date {cell(row, mapping, 'issued_date')!r} — row ignored",
                    )
                )
                continue
            grouped[emp_no].append(
                Issuance(
                    employee_number=emp_no,
                    item_code=code,
                    issued_date=issued,
                    quantity=parse_int(cell(row, mapping, "quantity"), 1) or 1,
                    size=parse_text(cell(row, mapping, "size")),
                    issued_by=parse_text(cell(row, mapping, "issued_by")),
                    cycle_months=parse_int(cell(row, mapping, "cycle_months")),
                    notes=parse_text(cell(row, mapping, "notes")),
                    row=number,
                )
            )
        snap.issuances = dict(grouped)


# --------------------------------------------------------------------- helpers


def _find_sheet(wb: Workbook, canonical: str):
    aliases = {normalise_header(a) for a in schema.SHEET_ALIASES.get(canonical, ())}
    aliases.add(normalise_header(canonical))
    for title in wb.sheetnames:
        if normalise_header(title) in aliases:
            return wb[title]
    return None


def _append_order(ws, *, create_if_missing: bool = True) -> list[str]:
    """Match the sheet's existing header order so appended rows line up."""
    header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not header:
        if create_if_missing:
            ws.append(list(schema.ISSUANCE_HEADERS))
        return list(schema.ISSUANCE_WRITE_ORDER)
    mapping, _ = map_headers(header, schema.ISSUANCE_COLUMNS)
    width = len(header)
    order: list[str] = [""] * width
    for field_name, idx in mapping.items():
        if idx < width:
            order[idx] = field_name
    return order


def _issuance_row(iss: Issuance, order: Sequence[str]) -> list[Any]:
    values = {
        "employee_number": iss.employee_number,
        "item_code": iss.item_code,
        "issued_date": iss.issued_date,
        "quantity": iss.quantity,
        "size": iss.size,
        "issued_by": iss.issued_by,
        "cycle_months": iss.cycle_months,
        "notes": iss.notes,
    }
    return [values.get(field_name) if field_name else None for field_name in order]


def _employee_status(value: Any) -> EmployeeStatus:
    text = normalise_header(value)
    if not text:
        return EmployeeStatus.ACTIVE
    if text in {"left", "terminated", "resigned", "exited", "inactive", "no", "false", "0"}:
        return EmployeeStatus.LEFT
    if text in {"onleave", "leave", "suspended", "maternity", "sabbatical"}:
        return EmployeeStatus.ON_LEAVE
    return EmployeeStatus.ACTIVE


def _size_key(explicit: Any, category: Any) -> Optional[str]:
    value = parse_text(explicit) or parse_text(category)
    if not value:
        return None
    key = normalise_header(value)
    for known in ("shirt", "trouser", "blazer", "shoe"):
        if known in key:
            return known
    return None


def _quiet_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def create_blank_workbook(path: str | Path) -> Path:
    """Create an empty workbook with the expected sheets and headers."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    for sheet, headers in (
        (schema.EMPLOYEES_SHEET, schema.EMPLOYEE_HEADERS),
        (schema.ITEMS_SHEET, schema.ITEM_HEADERS),
        (schema.ENTITLEMENTS_SHEET, schema.ENTITLEMENT_HEADERS),
        (schema.ISSUANCES_SHEET, schema.ISSUANCE_HEADERS),
    ):
        wb.create_sheet(sheet).append(list(headers))
    wb.save(target)
    wb.close()
    return target
