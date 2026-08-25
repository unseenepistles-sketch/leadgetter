"""The due-date engine.

Pure functions over plain values — no I/O, no Excel, no database. This is where all
the business risk lives, so it is the part that is exhaustively unit-tested.

The renewal rule: an employee is entitled to a set of items by role. Each item has
its own cycle (12 months for shirts and trousers, 24 for blazers). The clock starts
at the last issuance, or at the join date if the item was never issued.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Optional, Sequence

from .models import (
    Employee,
    Entitlement,
    Issuance,
    ItemStatus,
    UniformItem,
    UniformStatus,
)


@dataclass(frozen=True, slots=True)
class Policy:
    """Tunable thresholds. Defaults match what the client described."""

    #: How far ahead a renewal is flagged. Six months — this is the procurement
    #: heads-up, since stores need lead time to order stock.
    due_soon_days: int = 180
    #: Grace after the due date before it escalates from `due` to `overdue`.
    overdue_grace_days: int = 30
    #: How long after joining before a never-issued item counts as missed rather
    #: than merely outstanding.
    first_issue_grace_days: int = 30


def add_months(start: date, months: int) -> date:
    """Add whole months, clamping to the end of the target month.

    ``31 Jan + 1 month`` is 28/29 Feb, and ``29 Feb + 12 months`` is 28 Feb — which
    is why this exists rather than ``timedelta(days=365)``, a mistake that silently
    drifts a day every leap year and lands renewals on the wrong month end.
    """
    total = (start.year * 12 + start.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def compute_status(
    employee: Employee,
    item: UniformItem,
    entitlement: Entitlement,
    last_issuance: Optional[Issuance],
    today: date,
    policy: Policy = Policy(),
    override: Optional[date] = None,
) -> ItemStatus:
    """Work out where one employee stands on one entitled item.

    ``override`` is a manually authorised next-due date. When present it replaces
    the calculated one entirely — that is the point of an override — but the item
    is still classified normally against it, so an overridden date that has passed
    still shows as overdue rather than quietly disappearing.
    """
    base = dict(
        employee_number=employee.employee_number,
        employee_name=employee.full_name,
        item_code=item.item_code,
        item_name=item.name,
        quantity=entitlement.quantity or item.default_quantity,
        size=_size_for(employee, item, last_issuance),
    )
    cycle = _cycle_for(item, last_issuance)

    # Bad data must never produce a confident-looking reminder. Flag it instead.
    if employee.problems:
        return ItemStatus(
            **base,
            status=UniformStatus.NEEDS_REVIEW,
            cycle_months=cycle,
            reason="; ".join(employee.problems),
        )
    if last_issuance is None and employee.join_date is None:
        return ItemStatus(
            **base,
            status=UniformStatus.NEEDS_REVIEW,
            cycle_months=cycle,
            reason="no join date and no issuance on record",
        )

    if override is not None:
        return _classify(base, cycle, last_issuance, override, today, policy,
                         reason="renewal date set manually")

    if last_issuance is None:
        # No issuance row exists at all. This employee is invisible to any query
        # over the issuance log, which is exactly why the engine walks entitlements.
        assert employee.join_date is not None
        missed_from = employee.join_date + _days(policy.first_issue_grace_days)
        status = (
            UniformStatus.NEVER_ISSUED if today > missed_from else UniformStatus.DUE
        )
        next_due = employee.join_date
        return ItemStatus(
            **base,
            status=status,
            cycle_months=cycle,
            last_issued=None,
            next_due=next_due,
            days_until_due=(next_due - today).days,
            reason=(
                "entitled but never issued" if status is UniformStatus.NEVER_ISSUED
                else "awaiting first issue"
            ),
        )

    return _classify(base, cycle, last_issuance,
                     add_months(last_issuance.issued_date, cycle), today, policy)


def _classify(base, cycle, last_issuance, next_due, today, policy, reason=None) -> ItemStatus:
    days = (next_due - today).days
    if today > next_due + _days(policy.overdue_grace_days):
        status = UniformStatus.OVERDUE
    elif today >= next_due:
        status = UniformStatus.DUE
    elif days <= policy.due_soon_days:
        status = UniformStatus.DUE_SOON
    else:
        status = UniformStatus.OK
    return ItemStatus(
        **base,
        status=status,
        cycle_months=cycle,
        last_issued=last_issuance.issued_date if last_issuance else None,
        next_due=next_due,
        days_until_due=days,
        reason=reason,
    )


def statuses_for_employee(
    employee: Employee,
    entitlements: Sequence[Entitlement],
    items: Mapping[str, UniformItem],
    issuances: Iterable[Issuance],
    today: date,
    policy: Policy = Policy(),
    overrides: Optional[Mapping[str, date]] = None,
) -> list[ItemStatus]:
    """Every entitled item for one employee, including ones never issued."""
    latest = latest_issuance_by_item(issuances)
    overrides = overrides or {}
    out: list[ItemStatus] = []
    for ent in entitlements:
        item = items.get(ent.item_code)
        if item is None or not item.active:
            continue
        out.append(
            compute_status(employee, item, ent, latest.get(ent.item_code), today, policy,
                           override=overrides.get(ent.item_code))
        )
    return out


def latest_issuance_by_item(issuances: Iterable[Issuance]) -> dict[str, Issuance]:
    """Most recent issuance per item code.

    Ties on date fall back to sheet row order, so the lower row in the spreadsheet
    loses — matching the append-only way the client actually uses the workbook.
    """
    latest: dict[str, Issuance] = {}
    for iss in issuances:
        current = latest.get(iss.item_code)
        if current is None or _sort_key(iss) > _sort_key(current):
            latest[iss.item_code] = iss
    return latest


def _sort_key(iss: Issuance) -> tuple[date, int]:
    return (iss.issued_date, iss.row or 0)


def _cycle_for(item: UniformItem, last: Optional[Issuance]) -> int:
    """Prefer the cycle snapshotted at issue time so history is never rewritten."""
    if last is not None and last.cycle_months:
        return last.cycle_months
    return item.renewal_cycle_months


def _size_for(
    employee: Employee, item: UniformItem, last: Optional[Issuance]
) -> Optional[str]:
    if last is not None and last.size:
        return last.size
    if item.size_key:
        return employee.sizes.get(item.size_key)
    return None


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)
