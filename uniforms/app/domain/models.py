"""Domain types.

Plain dataclasses, deliberately free of any storage concern. The Excel workbook is
the system of record; these are just what a row looks like once parsed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class EmployeeStatus(str, Enum):
    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    LEFT = "left"


class OrderStatus(str, Enum):
    """Where an order stands between being raised and reaching the person.

    The real sequence is: she raises an order against a PR number, the tailor
    visits to take measurements, and the tailor returns with the clothes —
    sometimes all of them, sometimes some now and the rest later. Measurement is
    a distinct stage because an order can sit there for weeks, and "the tailor
    has not been yet" is a different problem from "the tailor has not delivered".
    """

    AWAITING_MEASUREMENT = "awaiting_measurement"
    PENDING = "pending"
    PARTIAL = "partially_delivered"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


ORDER_LABELS = {
    OrderStatus.AWAITING_MEASUREMENT: "Awaiting measurement",
    OrderStatus.PENDING: "Awaiting delivery",
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
    size: Optional[str] = None
    #: The day the tailor came to take measurements. Order-level, so it is the
    #: same on every row of a PR; nothing is delivered before it is set.
    measured_date: Optional[date] = None
    supplier_ref: Optional[str] = None
    notes: Optional[str] = None
    cancelled: bool = False
    row: Optional[int] = None

    @property
    def pr_number(self) -> str:
        """What she calls it. The PR number *is* the order id."""
        return self.order_id


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
        return self.status in (
            OrderStatus.AWAITING_MEASUREMENT,
            OrderStatus.PENDING,
            OrderStatus.PARTIAL,
        )


@dataclass(frozen=True, slots=True)
class OrderSummary:
    """A whole PR rolled up: what was asked for, what has arrived, where it is.

    She works at this level — one PR number, one tailor, one measurement visit —
    while the individual lines are what actually get delivered and tracked.
    """

    pr_number: str
    raised: date
    measured: Optional[date]
    tailor: Optional[str]
    notes: Optional[str]
    lines: tuple["OrderLine", ...]
    status: OrderStatus

    @property
    def ordered(self) -> int:
        return sum(l.order.quantity for l in self.lines)

    @property
    def delivered(self) -> int:
        return sum(l.delivered for l in self.lines)

    @property
    def outstanding(self) -> int:
        return max(0, self.ordered - self.delivered)

    @property
    def people(self) -> int:
        return len({l.order.employee_number for l in self.lines})

    @property
    def first_delivery(self) -> Optional[date]:
        dates = [l.last_delivery for l in self.lines if l.last_delivery]
        return min(dates) if dates else None

    @property
    def last_delivery(self) -> Optional[date]:
        dates = [l.last_delivery for l in self.lines if l.last_delivery]
        return max(dates) if dates else None

    @property
    def is_open(self) -> bool:
        return self.status not in (OrderStatus.DELIVERED, OrderStatus.CANCELLED)


def order_status(
    quantity: int,
    delivered: int,
    cancelled: bool = False,
    measured: bool = True,
) -> OrderStatus:
    """Where this line stands.

    ``measured`` defaults to True so that a workbook with no measurement column —
    an older one, or one where she has not filled it in — behaves exactly as it
    did before rather than showing every order stuck at the measuring stage.
    A delivery always wins over a missing measurement date: if the clothes are
    here, the tailor plainly came, whatever the sheet says.
    """
    if cancelled:
        return OrderStatus.CANCELLED
    if delivered <= 0:
        return OrderStatus.AWAITING_MEASUREMENT if not measured else OrderStatus.PENDING
    if delivered < quantity:
        return OrderStatus.PARTIAL
    return OrderStatus.DELIVERED


@dataclass(frozen=True, slots=True)
class RenewalOverride:
    """A manually set next-due date, replacing the calculated one.

    The spec requires overriding automatic renewal dates "with proper
    authorization", so who authorised it and why are recorded alongside the date
    — an override without that context is indistinguishable from a mistake.
    """

    employee_number: str
    item_code: str
    next_due: Optional[date]
    reason: str
    authorised_by: str
    set_at: date
    active: bool = True
    row: Optional[int] = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.employee_number, self.item_code)


@dataclass(frozen=True, slots=True)
class Change:
    """One field edit, for the audit trail.

    Edits are the point at which a system of record stops being trustworthy
    without history: every change records what it was before.
    """

    at: datetime
    who: str
    record_type: str
    record_id: str
    field: str
    old_value: str
    new_value: str
    reason: Optional[str] = None
