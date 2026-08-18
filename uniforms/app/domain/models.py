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


class OrderStatus(str, Enum):
    """Where an order stands between being placed and reaching the person."""

    AWAITING = "awaiting_delivery"
    PARTIAL = "partially_delivered"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


ORDER_LABELS = {
    OrderStatus.AWAITING: "Awaiting delivery",
    OrderStatus.PARTIAL: "Partially delivered",
    OrderStatus.DELIVERED: "Delivered",
    OrderStatus.CANCELLED: "Cancelled",
}


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
    #: The order this delivery fulfils, when it came from one. Deliveries recorded
    #: at the counter without a prior order simply leave it blank.
    order_id: Optional[str] = None
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


@dataclass(frozen=True, slots=True)
class Order:
    """A request for uniform items. Fulfilled by one or more issuances.

    Ordering and handing over are separate events, often weeks apart, and an
    order frequently arrives in parts — two jackets ordered, one delivered. The
    renewal clock deliberately runs from the issuance, not from this: the cycle
    starts when the person actually has the garment.
    """

    order_id: str
    employee_number: str
    item_code: str
    ordered_date: date
    quantity: int = 1
    supplier_ref: Optional[str] = None
    notes: Optional[str] = None
    cancelled: bool = False
    row: Optional[int] = None


@dataclass(frozen=True, slots=True)
class OrderLine:
    """An order plus what has actually arrived against it."""

    order: Order
    employee_name: str
    role: Optional[str]
    item_name: str
    delivered: int
    status: OrderStatus
    last_delivery: Optional[date] = None
    next_due: Optional[date] = None

    @property
    def pending(self) -> int:
        return max(0, self.order.quantity - self.delivered)

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.AWAITING, OrderStatus.PARTIAL)


def order_status(quantity: int, delivered: int, cancelled: bool = False) -> OrderStatus:
    if cancelled:
        return OrderStatus.CANCELLED
    if delivered <= 0:
        return OrderStatus.AWAITING
    if delivered < quantity:
        return OrderStatus.PARTIAL
    return OrderStatus.DELIVERED
