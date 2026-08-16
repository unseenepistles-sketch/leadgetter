"""Domain types.

Plain dataclasses, deliberately free of any storage concern. The Excel workbook is
the system of record; these are just what a row looks like once parsed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class EmployeeStatus(str, Enum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    LEFT = "left"


class UniformStatus(str, Enum):
    """Where an employee stands on one entitled item."""

    OK = "ok"
    DUE_SOON = "due_soon"
    DUE = "due"
    OVERDUE = "overdue"
    #: Entitled to the item but there is no issuance row at all — the "missed" case.
    NEVER_ISSUED = "never_issued"
    #: Data too poor to judge (no join date, unparseable dates). Never triggers a reminder.
    NEEDS_REVIEW = "needs_review"


#: Statuses that represent an outstanding obligation, in escalation order.
OUTSTANDING = (
    UniformStatus.NEVER_ISSUED,
    UniformStatus.OVERDUE,
    UniformStatus.DUE,
    UniformStatus.DUE_SOON,
)


@dataclass(frozen=True, slots=True)
class Employee:
    employee_number: str
    full_name: str
    email: Optional[str] = None
    manager_email: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    join_date: Optional[date] = None
    status: EmployeeStatus = EmployeeStatus.ACTIVE
    sizes: dict[str, str] = field(default_factory=dict)
    #: Populated by the parser when a row could not be fully understood.
    problems: tuple[str, ...] = ()
    #: 1-based row in the Employees sheet, so an edit can find its way home.
    row: Optional[int] = None

    @property
    def is_active(self) -> bool:
        return self.status is EmployeeStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class UniformItem:
    item_code: str
    name: str
    category: Optional[str] = None
    #: 12 for shirts/trousers, 24 for blazers. Lives in the sheet, never in code.
    renewal_cycle_months: int = 12
    default_quantity: int = 1
    #: Which size field on the employee prefills this item's size, e.g. "shirt".
    size_key: Optional[str] = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class Entitlement:
    """A rule: this role gets this many of this item. Not materialised per employee."""

    role: str
    item_code: str
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class Issuance:
    employee_number: str
    item_code: str
    issued_date: date
    quantity: int = 1
    size: Optional[str] = None
    issued_by: Optional[str] = None
    #: Snapshot of the cycle in force when issued, so later policy changes never
    #: rewrite history.
    cycle_months: Optional[int] = None
    notes: Optional[str] = None
    row: Optional[int] = None


@dataclass(frozen=True, slots=True)
class ItemStatus:
    """The computed answer for one employee and one entitled item."""

    employee_number: str
    employee_name: str
    item_code: str
    item_name: str
    quantity: int
    status: UniformStatus
    cycle_months: int
    last_issued: Optional[date] = None
    next_due: Optional[date] = None
    days_until_due: Optional[int] = None
    size: Optional[str] = None
    reason: Optional[str] = None

    @property
    def is_outstanding(self) -> bool:
        return self.status in OUTSTANDING
