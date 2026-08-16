"""The service layer: everything the API and the UI both need.

Pages and JSON endpoints are peers — both call in here, neither calls the other
over HTTP. That keeps the business logic in one place and leaves the door open for
a different front end later without touching any of it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence

from .domain.due import Policy, statuses_for_employee
from .domain.models import (
    Employee,
    Issuance,
    ItemStatus,
    UniformStatus,
)
from .excelstore.workbook import Snapshot, WorkbookStore

log = logging.getLogger("uniforms.service")


class NotFound(LookupError):
    pass


class ValidationError(ValueError):
    """The submission is not something we can record."""


@dataclass(frozen=True, slots=True)
class Page:
    items: list
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))

    def as_dict(self, serialiser) -> dict:
        return {
            "items": [serialiser(i) for i in self.items],
            "total": self.total,
            "page": self.page,
            "per_page": self.per_page,
            "pages": self.pages,
        }


class UniformService:
    def __init__(self, store: WorkbookStore, policy: Policy | None = None) -> None:
        self.store = store
        self.policy = policy or Policy()

    # ------------------------------------------------------------------ basics

    @property
    def snapshot(self) -> Snapshot:
        return self.store.snapshot

    def employee(self, employee_number: str) -> Employee:
        emp = self.snapshot.employees.get(employee_number)
        if emp is None:
            raise NotFound(f"no employee {employee_number!r}")
        return emp

    def employees(
        self,
        *,
        q: Optional[str] = None,
        department: Optional[str] = None,
        role: Optional[str] = None,
        active_only: bool = False,
        page: int = 1,
        per_page: int = 50,
    ) -> Page:
        rows = list(self.snapshot.employees.values())
        if active_only:
            rows = [e for e in rows if e.is_active]
        if department:
            rows = [e for e in rows if (e.department or "").lower() == department.lower()]
        if role:
            rows = [e for e in rows if (e.role or "").lower() == role.lower()]
        if q:
            needle = q.strip().lower()
            rows = [
                e for e in rows
                if needle in e.full_name.lower()
                or needle in e.employee_number.lower()
                or needle in (e.email or "").lower()
            ]
        rows.sort(key=lambda e: e.full_name.lower())
        return _paginate(rows, page, per_page)

    def departments(self) -> list[str]:
        return sorted({e.department for e in self.snapshot.employees.values() if e.department})

    def roles(self) -> list[str]:
        return sorted({e.role for e in self.snapshot.employees.values() if e.role})

    # ----------------------------------------------------------------- statuses

    def statuses_for(self, employee_number: str, today: Optional[date] = None) -> list[ItemStatus]:
        emp = self.employee(employee_number)
        return self._statuses(emp, today or date.today())

    def _statuses(self, emp: Employee, today: date) -> list[ItemStatus]:
        snap = self.snapshot
        return statuses_for_employee(
            emp,
            snap.entitlements_for(emp.role),
            snap.items,
            snap.issuances_for(emp.employee_number),
            today,
            self.policy,
        )

    def all_statuses(
        self, today: Optional[date] = None, *, active_only: bool = True
    ) -> Iterable[ItemStatus]:
        """Every employee against every entitled item.

        Deliberately walks employees rather than issuances: someone who was never
        issued a blazer has no issuance row at all, so scanning the log could never
        find them — and that is precisely the "who was missed" question.
        """
        today = today or date.today()
        for emp in self.snapshot.employees.values():
            if active_only and not emp.is_active:
                continue
            yield from self._statuses(emp, today)

    def report(
        self,
        state: Optional[str] = None,
        *,
        department: Optional[str] = None,
        today: Optional[date] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> Page:
        wanted = _parse_state(state)
        employees = self.snapshot.employees
        rows = [
            s for s in self.all_statuses(today)
            if (wanted is None or s.status is wanted)
            and (
                not department
                or (employees[s.employee_number].department or "").lower() == department.lower()
            )
        ]
        rows.sort(key=lambda s: (_severity(s.status), s.next_due or date.max, s.employee_name))
        return _paginate(rows, page, per_page)

    def summary(self, today: Optional[date] = None) -> dict[str, int]:
        counts = {s.value: 0 for s in UniformStatus}
        for status in self.all_statuses(today):
            counts[status.status.value] += 1
        counts["employees"] = sum(1 for e in self.snapshot.employees.values() if e.is_active)
        counts["outstanding"] = sum(
            counts[s.value] for s in
            (UniformStatus.NEVER_ISSUED, UniformStatus.OVERDUE, UniformStatus.DUE)
        )
        return counts

    # ------------------------------------------------------------------- writes

    def record_issuance(
        self,
        employee_number: str,
        item_code: str,
        *,
        issued_date: Optional[date] = None,
        quantity: Optional[int] = None,
        size: Optional[str] = None,
        issued_by: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Issuance:
        emp = self.employee(employee_number)
        item = self.snapshot.items.get(item_code)
        if item is None:
            raise ValidationError(f"unknown item code {item_code!r}")

        when = issued_date or date.today()
        if when > date.today():
            raise ValidationError("issue date cannot be in the future")

        entitlement = next(
            (e for e in self.snapshot.entitlements_for(emp.role) if e.item_code == item_code),
            None,
        )
        qty = quantity or (entitlement.quantity if entitlement else item.default_quantity)
        if qty < 1:
            raise ValidationError("quantity must be at least 1")

        issuance = Issuance(
            employee_number=employee_number,
            item_code=item_code,
            issued_date=when,
            quantity=qty,
            size=size or (item.size_key and emp.sizes.get(item.size_key)) or None,
            issued_by=issued_by,
            # Snapshot the cycle so a later policy change never rewrites history.
            cycle_months=item.renewal_cycle_months,
            notes=notes,
        )
        self.store.append_issuance(issuance)
        return issuance

    def record_bulk(
        self,
        employee_numbers: Sequence[str],
        item_codes: Sequence[str],
        *,
        issued_date: Optional[date] = None,
        issued_by: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> list[Issuance]:
        """Issue the same kit to many people — a new intake in one submit."""
        if not employee_numbers:
            raise ValidationError("select at least one employee")
        if not item_codes:
            raise ValidationError("select at least one item")

        when = issued_date or date.today()
        if when > date.today():
            raise ValidationError("issue date cannot be in the future")

        created: list[Issuance] = []
        for emp_no in employee_numbers:
            emp = self.employee(emp_no)
            entitlements = {e.item_code: e for e in self.snapshot.entitlements_for(emp.role)}
            for code in item_codes:
                item = self.snapshot.items.get(code)
                if item is None:
                    raise ValidationError(f"unknown item code {code!r}")
                ent = entitlements.get(code)
                created.append(
                    Issuance(
                        employee_number=emp_no,
                        item_code=code,
                        issued_date=when,
                        quantity=ent.quantity if ent else item.default_quantity,
                        size=item.size_key and emp.sizes.get(item.size_key) or None,
                        issued_by=issued_by,
                        cycle_months=item.renewal_cycle_months,
                        notes=notes,
                    )
                )
        self.store.append_issuances(created)
        return created

    # ------------------------------------------------------------ data quality

    def data_quality(self) -> list[dict]:
        """Everything the parser could not make sense of, for the client to fix."""
        return [
            {"sheet": p.sheet, "row": p.row, "message": p.message}
            for p in self.snapshot.problems
        ]


# --------------------------------------------------------------------- helpers

_SEVERITY = {
    UniformStatus.NEVER_ISSUED: 0,
    UniformStatus.OVERDUE: 1,
    UniformStatus.DUE: 2,
    UniformStatus.DUE_SOON: 3,
    UniformStatus.NEEDS_REVIEW: 4,
    UniformStatus.OK: 5,
}


def _severity(status: UniformStatus) -> int:
    return _SEVERITY.get(status, 9)


def _parse_state(state: Optional[str]) -> Optional[UniformStatus]:
    if not state or state == "all":
        return None
    try:
        return UniformStatus(state)
    except ValueError as exc:
        raise ValidationError(f"unknown state {state!r}") from exc


def _paginate(rows: list, page: int, per_page: int) -> Page:
    per_page = max(1, min(per_page, 500))
    page = max(1, page)
    start = (page - 1) * per_page
    return Page(rows[start : start + per_page], len(rows), page, per_page)
