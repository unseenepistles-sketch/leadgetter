"""Reminder stages: when each fires, and who hears about it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..domain.models import ItemStatus, UniformStatus

EMPLOYEE = "employee"
MANAGER = "manager"
STORES = "stores"


@dataclass(frozen=True, slots=True)
class Stage:
    code: str
    label: str
    audience: tuple[str, ...]
    blurb: str


#: The six-month heads-up goes to stores as well as the employee. Its real job is
#: procurement lead time — someone has to order the stock before it is needed.
T6M = Stage("T6M", "Due in 6 months", (STORES, EMPLOYEE),
            "is due for renewal in about six months")
T1M = Stage("T1M", "Due in 1 month", (EMPLOYEE,),
            "is due for renewal next month")
DUE = Stage("DUE", "Due now", (EMPLOYEE,),
            "is due for renewal now")
OVERDUE_30 = Stage("OVERDUE_30", "Overdue", (EMPLOYEE, MANAGER),
                   "is overdue for renewal")
NEVER_ISSUED = Stage("NEVER_ISSUED", "Never issued", (STORES, MANAGER),
                     "has never been issued and is outstanding")

ALL_STAGES = (T6M, T1M, DUE, OVERDUE_30, NEVER_ISSUED)
BY_CODE = {s.code: s for s in ALL_STAGES}


def stage_for(status: ItemStatus, *, due_soon_days: int = 180) -> Optional[Stage]:
    """Which single stage applies to this item today, if any.

    Each stage carries its own dedupe key, so as time passes an item walks
    T6M -> T1M -> DUE -> OVERDUE_30 and each fires exactly once.
    """
    if status.status is UniformStatus.NEEDS_REVIEW:
        return None  # bad data must never produce a confident-sounding reminder
    if status.status is UniformStatus.NEVER_ISSUED:
        return NEVER_ISSUED
    if status.days_until_due is None:
        return None

    days = status.days_until_due
    if days <= -30:
        return OVERDUE_30
    if days <= 0:
        return DUE
    if days <= 30:
        return T1M
    if days <= due_soon_days:
        return T6M
    return None
