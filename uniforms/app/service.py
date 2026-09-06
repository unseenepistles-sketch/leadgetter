"""The service layer: everything the API and the UI both need.

Pages and JSON endpoints are peers — both call in here, neither calls the other
over HTTP. That keeps the business logic in one place and leaves the door open for
a different front end later without touching any of it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

from .domain.due import Policy, add_months, statuses_for_employee
from .domain.models import (
    Change,
    Employee,
    Issuance,
    ItemStatus,
    Order,
    OrderLine,
    OrderStatus,
    OrderSummary,
    RenewalOverride,
    UniformStatus,
    order_status,
)
from .domain.models import UniformItem
from .excelstore.schema import (
    EMPLOYEES_SHEET,
    ISSUANCES_SHEET,
    ITEMS_SHEET,
    ORDERS_SHEET,
    SIZE_FIELDS,
)
from .excelstore.workbook import Snapshot, WorkbookStore

#: employee sheet column -> the size key held on the record.
SIZE_KEYS = dict(SIZE_FIELDS)

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
        overrides = {
            code: o.next_due
            for (num, code), o in snap.overrides.items()
            if num == emp.employee_number and o.active and o.next_due
        }
        return statuses_for_employee(
            emp,
            snap.entitlements_for(emp.role),
            snap.items,
            snap.issuances_for(emp.employee_number),
            today,
            self.policy,
            overrides=overrides,
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

    # ------------------------------------------------------------------ orders

    def order_lines(
        self,
        *,
        status: Optional[str] = None,
        item_code: Optional[str] = None,
        employee_number: Optional[str] = None,
        open_only: bool = False,
    ) -> list[OrderLine]:
        rows = [_order_line(self, o) for o in self.snapshot.orders.values()]
        if employee_number:
            rows = [r for r in rows if r.order.employee_number == employee_number]
        if item_code:
            rows = [r for r in rows if r.order.item_code == item_code]
        if open_only:
            rows = [r for r in rows if r.is_open]
        if status and status != "all":
            try:
                wanted = OrderStatus(status)
            except ValueError as exc:
                raise ValidationError(f"unknown order status {status!r}") from exc
            rows = [r for r in rows if r.status is wanted]
        rows.sort(key=lambda r: (r.order.ordered_date, r.employee_name), reverse=True)
        return rows

    def order_line(self, pr_number: str, employee_number: str, item_code: str) -> OrderLine:
        """One line of one order. A PR alone does not identify a line — the same
        PR number covers every person on the bulk order."""
        order = self.snapshot.orders.get((pr_number, employee_number, item_code))
        if order is None:
            raise NotFound(f"no line for {item_code} / {employee_number} on {pr_number}")
        return _order_line(self, order)

    def order(self, pr_number: str) -> "OrderSummary":
        """A whole PR: its lines, its dates and where it has got to."""
        lines = [
            _order_line(self, o)
            for o in self.snapshot.orders.values()
            if o.order_id == pr_number
        ]
        if not lines:
            raise NotFound(f"no order {pr_number!r}")
        return _summarise(pr_number, lines)

    def orders(self, *, status: Optional[str] = None) -> list["OrderSummary"]:
        """Every PR, newest first — the order log as she thinks of it."""
        grouped: dict[str, list[OrderLine]] = {}
        for order in self.snapshot.orders.values():
            grouped.setdefault(order.order_id, []).append(_order_line(self, order))
        rows = [_summarise(pr, lines) for pr, lines in grouped.items()]
        if status and status != "all":
            try:
                wanted = OrderStatus(status)
            except ValueError as exc:
                raise ValidationError(f"unknown order status {status!r}") from exc
            rows = [r for r in rows if r.status is wanted]
        rows.sort(key=lambda r: (r.raised, r.pr_number), reverse=True)
        return rows

    def outstanding_lines(self, today: Optional[date] = None) -> list[dict]:
        """Every garment ordered and not yet handed over, longest wait first.

        The tailor routinely delivers part of an order and says the rest will
        follow, and "the rest" is exactly what gets forgotten. The wait is
        measured from the measurement visit, or from the last part-delivery if
        there has been one — that is the point from which the balance was owed.
        """
        today = today or date.today()
        rows: list[dict] = []
        for summary in self.orders():
            if summary.measured is None:
                continue          # nothing is owed until the tailor has taken sizes
            for line in summary.lines:
                if line.order.cancelled or line.pending <= 0:
                    continue
                since = line.last_delivery or summary.measured
                rows.append({
                    "pr_number": summary.pr_number,
                    "employee_number": line.order.employee_number,
                    "employee_name": line.employee_name,
                    "role": line.role,
                    "item_code": line.order.item_code,
                    "item_name": line.item_name,
                    "size": line.order.size,
                    "ordered": line.order.quantity,
                    "delivered": line.delivered,
                    "outstanding": line.pending,
                    "promised_since": since,
                    "waiting_days": (today - since).days,
                    "part_delivered": line.delivered > 0,
                })
        rows.sort(key=lambda r: r["waiting_days"], reverse=True)
        return rows

    def pending_delivery(self) -> int:
        """Pieces ordered that have not arrived — her 'items pending delivery'."""
        return sum(line.pending for line in self.order_lines(open_only=True))

    def place_order(
        self,
        pr_number: str,
        lines: Sequence[dict],
        *,
        ordered_date: Optional[date] = None,
        tailor: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> "OrderSummary":
        """Raise one order against one PR number.

        A real order is a bulk one — thirty-five people, a hundred and sixty-eight
        lines, one PR number on the paperwork. Each ``line`` is
        ``{"employee_number", "item_code", "quantity", "size"}``. Nothing is
        measured or delivered yet; those are separate events that follow.
        """
        pr = (pr_number or "").strip()
        if not pr:
            raise ValidationError("a PR number is required")
        if any(o.order_id == pr for o in self.snapshot.orders.values()):
            raise ValidationError(f"{pr} has already been used for another order")

        when = ordered_date or date.today()
        if when > date.today():
            raise ValidationError("the order date cannot be in the future")

        created: list[Order] = []
        seen: set[tuple[str, str]] = set()
        for raw in lines:
            emp_no = str(raw.get("employee_number") or "").strip()
            code = str(raw.get("item_code") or "").strip()
            qty = int(raw.get("quantity") or 0)
            if qty <= 0:
                continue
            self.employee(emp_no)
            if code not in self.snapshot.items:
                raise ValidationError(f"unknown item code {code!r}")
            if (emp_no, code) in seen:
                raise ValidationError(
                    f"{emp_no} appears twice for {code} on this order — combine them"
                )
            seen.add((emp_no, code))
            created.append(
                Order(
                    order_id=pr,
                    employee_number=emp_no,
                    item_code=code,
                    ordered_date=when,
                    quantity=qty,
                    size=(str(raw.get("size")).strip() or None) if raw.get("size") else None,
                    supplier_ref=tailor,
                    notes=notes,
                )
            )
        if not created:
            raise ValidationError("an order needs at least one item with a quantity")

        self.store.append_orders(created)
        for order in created:
            self.snapshot.orders[(pr, order.employee_number, order.item_code)] = order
        return self.order(pr)

    def record_measurement(
        self,
        pr_number: str,
        measured_date: Optional[date] = None,
        *,
        who: Optional[str] = None,
    ) -> "OrderSummary":
        """The day the tailor came to take sizes. One visit covers the whole PR."""
        summary = self.order(pr_number)
        when = measured_date or date.today()
        if when > date.today():
            raise ValidationError("the measurement date cannot be in the future")
        if when < summary.raised:
            raise ValidationError(
                f"the tailor cannot have measured before the order was raised "
                f"({summary.raised:%d %b %Y})"
            )

        for line in summary.lines:
            order = line.order
            if order.measured_date == when:
                continue
            key = {
                "order_id": pr_number,
                "employee_number": order.employee_number,
                "item_code": order.item_code,
            }
            self.store.update_by_key(ORDERS_SHEET, key, {"measured_date": when})
            self.snapshot.orders[(pr_number, order.employee_number, order.item_code)] = (
                replace(order, measured_date=when)
            )
            self._log(
                who, "order", f"{pr_number}/{order.employee_number}/{order.item_code}",
                {"measured_date": (order.measured_date, when)},
                "tailor measurement visit",
            )
        return self.order(pr_number)

    def receive_delivery(
        self,
        pr_number: str,
        employee_number: str,
        item_code: str,
        *,
        quantity: Optional[int] = None,
        received_date: Optional[date] = None,
        size: Optional[str] = None,
        received_by: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Issuance:
        """Book in what the tailor actually brought for one person and one item.

        This is the handover, and it starts the renewal clock — the cycle runs
        from when the person has the garment, not from when it was ordered.
        """
        line = self.order_line(pr_number, employee_number, item_code)
        if line.order.cancelled:
            raise ValidationError("that order was cancelled")
        if line.pending <= 0:
            raise ValidationError("that line is already fully delivered")

        qty = quantity if quantity is not None else line.pending
        if qty < 1:
            raise ValidationError("quantity must be at least 1")
        if qty > line.pending:
            raise ValidationError(
                f"only {line.pending} of {line.order.quantity} still outstanding"
            )

        when = received_date or date.today()
        if when > date.today():
            raise ValidationError("delivery date cannot be in the future")
        if when < line.order.ordered_date:
            raise ValidationError(
                f"the tailor cannot have delivered before the order was raised "
                f"({line.order.ordered_date:%d %b %Y})"
            )

        emp = self.employee(line.order.employee_number)
        item = self.snapshot.items[line.order.item_code]
        issuance = Issuance(
            employee_number=emp.employee_number,
            item_code=item.item_code,
            issued_date=when,
            quantity=qty,
            size=size
            or line.order.size
            or (item.size_key and emp.sizes.get(item.size_key))
            or None,
            issued_by=received_by,
            cycle_months=item.renewal_cycle_months,
            order_id=pr_number,
            notes=notes,
        )
        self.store.append_issuance(issuance)
        return issuance

    def receive_deliveries(
        self,
        pr_number: str,
        parts: Sequence[dict],
        *,
        received_date: Optional[date] = None,
        received_by: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> list[Issuance]:
        """One visit from the tailor, many garments booked in.

        The tailor arrives with a van, not with one shirt, so this is the normal
        case. Every part is validated before any of it is written — a bad line
        should not leave half a delivery recorded.
        """
        summary = self.order(pr_number)
        if summary.measured is None:
            raise ValidationError(
                "record the tailor's measurement visit before booking in a delivery"
            )

        when = received_date or date.today()
        planned: list[tuple[str, str, int]] = []
        for raw in parts:
            emp_no = str(raw.get("employee_number") or "").strip()
            code = str(raw.get("item_code") or "").strip()
            qty = int(raw.get("quantity") or 0)
            if qty <= 0:
                continue
            line = self.order_line(pr_number, emp_no, code)
            if qty > line.pending:
                raise ValidationError(
                    f"{line.employee_name} / {line.item_name}: only {line.pending} outstanding"
                )
            planned.append((emp_no, code, qty))
        if not planned:
            raise ValidationError("nothing was marked as delivered")

        return [
            self.receive_delivery(
                pr_number, emp_no, code, quantity=qty, received_date=when,
                received_by=received_by, notes=notes,
            )
            for emp_no, code, qty in planned
        ]

    def action_needed(self, today: Optional[date] = None, limit: int = 12) -> list[dict]:
        """One prioritised list: overdue, then upcoming renewals, then undelivered.

        Deliberately ordered the way the stores desk works through its day — the
        thing that is already late outranks the thing that has not shipped.
        """
        today = today or date.today()
        rows: list[dict] = []
        for s in self.all_statuses(today):
            if s.status in (UniformStatus.OVERDUE, UniformStatus.NEVER_ISSUED):
                rows.append({"kind": "renewal", "rank": 0, "status": s.status.value,
                             "employee_number": s.employee_number, "employee_name": s.employee_name,
                             "item_name": s.item_name, "detail": _renewal_detail(s),
                             "date": s.next_due})
            elif s.status in (UniformStatus.DUE, UniformStatus.DUE_SOON):
                rows.append({"kind": "renewal", "rank": 1, "status": s.status.value,
                             "employee_number": s.employee_number, "employee_name": s.employee_name,
                             "item_name": s.item_name, "detail": _renewal_detail(s),
                             "date": s.next_due})
        for line in self.order_lines(open_only=True):
            rows.append({"kind": "order", "rank": 2, "status": line.status.value,
                         "employee_number": line.order.employee_number,
                         "employee_name": line.employee_name, "item_name": line.item_name,
                         "detail": f"{line.delivered}/{line.order.quantity} delivered",
                         "date": line.order.ordered_date, "order_id": line.order.order_id})
        rows.sort(key=lambda r: (r["rank"], r["date"] or date.max, r["employee_name"]))
        return rows[:limit]

    def staff_by_category(self) -> list[dict]:
        """Headcount per staff category, which is how the department is organised."""
        counts: dict[str, int] = {}
        for emp in self.snapshot.employees.values():
            if emp.is_active:
                counts[emp.role or "Unassigned"] = counts.get(emp.role or "Unassigned", 0) + 1
        return [{"role": role, "count": n} for role, n in sorted(counts.items())]

    def recently_delivered(self, limit: int = 8, days: int = 30) -> list[OrderLine]:
        """Orders completed lately — the confirmation that the loop closed."""
        cutoff = date.today() - timedelta(days=days)
        rows = [
            l for l in self.order_lines()
            if l.status is OrderStatus.DELIVERED and l.last_delivery
            and l.last_delivery >= cutoff
        ]
        rows.sort(key=lambda l: l.last_delivery, reverse=True)
        return rows[:limit]

    def deliveries_for_order(self, order_id: str) -> list[Issuance]:
        """Each part-delivery against an order, with its own date."""
        rows = [
            i for group in self.snapshot.issuances.values()
            for i in group if i.order_id == order_id
        ]
        rows.sort(key=lambda i: i.issued_date)
        return rows

    # ------------------------------------------------- edit & administration

    def _log(self, who: str, record_type: str, record_id: str,
             changes: dict[str, tuple], reason: Optional[str] = None) -> int:
        """Record every field that actually moved, with its previous value."""
        now = datetime.now()
        rows = [
            Change(at=now, who=who or "unknown", record_type=record_type,
                   record_id=record_id, field=f,
                   old_value="" if old is None else str(old),
                   new_value="" if new is None else str(new), reason=reason)
            for f, (old, new) in changes.items()
        ]
        return self.store.record_changes(rows)

    def edit_employee(self, employee_number: str, fields: dict,
                      *, who: str = "", reason: Optional[str] = None) -> Employee:
        """Edit staff details. Only fields that actually change are written."""
        emp = self.employee(employee_number)
        allowed = {"full_name", "email", "manager_email", "department", "role",
                   "join_date", "status", "shirt_size", "trouser_size",
                   "blazer_size", "shoe_size"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValidationError(f"cannot edit {', '.join(sorted(unknown))}")

        sizes = dict(emp.sizes)
        changed: dict[str, tuple] = {}
        cells: dict[str, object] = {}
        updated = emp

        for name, raw in fields.items():
            if name.endswith("_size"):
                key = SIZE_KEYS[name]
                old, new = sizes.get(key), (str(raw).strip() or None) if raw else None
                if old == new:
                    continue
                if new:
                    sizes[key] = new
                else:
                    sizes.pop(key, None)
                changed[name], cells[name] = (old, new), new
                continue

            if name == "join_date":
                new_value = _as_date(raw)
            elif name == "status":
                new_value = _as_employee_status(raw)
            else:
                new_value = (str(raw).strip() or None) if raw is not None else None

            old_value = getattr(updated, name)
            if old_value == new_value:
                continue
            changed[name] = (old_value, new_value)
            cells[name] = new_value.value if name == "status" else new_value
            updated = replace(updated, **{name: new_value})

        if not changed:
            return emp

        updated = replace(updated, sizes=sizes)
        self.snapshot.employees[employee_number] = updated
        self.store.update_by_key(EMPLOYEES_SHEET, {"employee_number": employee_number}, cells)
        self._log(who, "employee", employee_number, changed, reason)
        return updated

    #: Fields that belong to the order rather than to one of its lines. They are
    #: stored on every row of the PR, so editing one writes them all.
    ORDER_HEADER_FIELDS = ("supplier_ref", "notes", "ordered_date", "measured_date")

    def edit_order_header(self, pr_number: str, fields: dict,
                          *, who: str = "", reason: Optional[str] = None) -> "OrderSummary":
        """Edit the order itself: the tailor, the remarks and the two dates.

        These are order-level facts held on every row of the PR, so a change is
        written down all of them — otherwise filtering the sheet by PR number
        would show the same order with two different tailors.
        """
        summary = self.order(pr_number)
        unknown = set(fields) - set(self.ORDER_HEADER_FIELDS)
        if unknown:
            raise ValidationError(f"cannot edit {', '.join(sorted(unknown))}")

        clean: dict[str, object] = {}
        for name, raw in fields.items():
            if name in ("ordered_date", "measured_date"):
                value = _as_date(raw) if raw not in (None, "") else None
                if name == "ordered_date":
                    if value is None:
                        raise ValidationError("the date raised is required")
                    if value > date.today():
                        raise ValidationError("the date raised cannot be in the future")
                elif value is not None:
                    if value > date.today():
                        raise ValidationError("the measurement date cannot be in the future")
                clean[name] = value
            else:
                clean[name] = (str(raw).strip() or None) if raw is not None else None

        raised = clean.get("ordered_date", summary.raised)
        measured = clean.get("measured_date", summary.measured)
        if measured is not None and raised is not None and measured < raised:
            raise ValidationError(
                "the tailor cannot have measured before the order was raised"
            )
        if measured is None and summary.delivered > 0 and "measured_date" in clean:
            raise ValidationError(
                "clear the deliveries first — the tailor plainly came if the clothes arrived"
            )

        for line in summary.lines:
            order = line.order
            changed = {f: (getattr(order, f), v) for f, v in clean.items()
                       if getattr(order, f) != v}
            if not changed:
                continue
            key = {"order_id": pr_number, "employee_number": order.employee_number,
                   "item_code": order.item_code}
            self.store.update_by_key(ORDERS_SHEET, key, dict(clean))
            self.snapshot.orders[(pr_number, order.employee_number, order.item_code)] = (
                replace(order, **clean)
            )
            self._log(who, "order",
                      f"{pr_number}/{order.employee_number}/{order.item_code}", changed, reason)
        return self.order(pr_number)

    def edit_order(self, pr_number: str, employee_number: str, item_code: str, fields: dict,
                   *, who: str = "", reason: Optional[str] = None) -> OrderLine:
        """Edit an order: quantity, requested date, supplier reference, remarks.

        Reducing the quantity below what has already arrived is refused — the
        delivered figure is derived from real handovers and cannot be contradicted.
        """
        line = self.order_line(pr_number, employee_number, item_code)
        order = line.order
        allowed = {"quantity", "ordered_date", "supplier_ref", "size", "notes", "cancelled"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValidationError(f"cannot edit {', '.join(sorted(unknown))}")

        changed: dict[str, tuple] = {}
        cells: dict[str, object] = {}
        updated = order

        for name, raw in fields.items():
            if name == "quantity":
                new_value = int(raw)
                if new_value < 1:
                    raise ValidationError("quantity must be at least 1")
                if new_value < line.delivered:
                    raise ValidationError(
                        f"{line.delivered} already delivered — quantity cannot be lower"
                    )
            elif name == "ordered_date":
                new_value = _as_date(raw)
                if new_value is None:
                    raise ValidationError("the requested date is required")
            elif name == "cancelled":
                new_value = bool(raw)
            else:
                new_value = (str(raw).strip() or None) if raw is not None else None

            old_value = getattr(updated, name)
            if old_value == new_value:
                continue
            changed[name] = (old_value, new_value)
            cells[name] = ("Yes" if new_value else None) if name == "cancelled" else new_value
            updated = replace(updated, **{name: new_value})

        if not changed:
            return line

        key = {"order_id": pr_number, "employee_number": employee_number,
               "item_code": item_code}
        self.snapshot.orders[(pr_number, employee_number, item_code)] = updated
        self.store.update_by_key(ORDERS_SHEET, key, cells)
        self._log(who, "order", f"{pr_number}/{employee_number}/{item_code}", changed, reason)
        return self.order_line(pr_number, employee_number, item_code)

    def edit_delivery(self, employee_number: str, row: int, fields: dict,
                      *, who: str = "", reason: Optional[str] = None) -> Issuance:
        """Edit a recorded handover — quantity, the date it arrived, size, remarks."""
        rows = self.snapshot.issuances.get(employee_number, [])
        index = next((i for i, x in enumerate(rows) if x.row == row), None)
        if index is None:
            raise NotFound(f"no delivery on row {row} for {employee_number}")
        issuance = rows[index]

        allowed = {"quantity", "issued_date", "size", "issued_by", "notes"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValidationError(f"cannot edit {', '.join(sorted(unknown))}")

        changed: dict[str, tuple] = {}
        cells: dict[str, object] = {}
        updated = issuance

        for name, raw in fields.items():
            if name == "quantity":
                new_value = int(raw)
                if new_value < 1:
                    raise ValidationError("quantity must be at least 1")
            elif name == "issued_date":
                new_value = _as_date(raw)
                if new_value is None:
                    raise ValidationError("the delivery date is required")
                if new_value > date.today():
                    raise ValidationError("delivery date cannot be in the future")
            else:
                new_value = (str(raw).strip() or None) if raw is not None else None

            old_value = getattr(updated, name)
            if old_value == new_value:
                continue
            changed[name] = (old_value, new_value)
            cells[name] = new_value
            updated = replace(updated, **{name: new_value})

        if not changed:
            return issuance

        rows[index] = updated
        self.store.update_row(
            ISSUANCES_SHEET, row, cells,
            # No natural key on this sheet, so prove the row is still the one we
            # read before writing to it.
            expect={"employee_number": issuance.employee_number,
                    "item_code": issuance.item_code},
        )
        self._log(who, "delivery", f"{employee_number} row {row}", changed, reason)
        return updated

    def edit_item(self, item_code: str, fields: dict,
                  *, who: str = "", reason: Optional[str] = None) -> UniformItem:
        """Edit the catalogue, including the renewal period.

        Changing a cycle only affects *future* issues: every issuance snapshots the
        cycle in force when it was made, so history is never silently rewritten.
        """
        item = self.snapshot.items.get(item_code)
        if item is None:
            raise NotFound(f"no item {item_code!r}")

        allowed = {"name", "renewal_cycle_months", "default_quantity", "active"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValidationError(f"cannot edit {', '.join(sorted(unknown))}")

        changed: dict[str, tuple] = {}
        cells: dict[str, object] = {}
        updated = item

        for name, raw in fields.items():
            if name in ("renewal_cycle_months", "default_quantity"):
                new_value = int(raw)
                if new_value < 1:
                    raise ValidationError(f"{name.replace('_', ' ')} must be at least 1")
            elif name == "active":
                new_value = bool(raw)
            else:
                new_value = str(raw).strip()
                if not new_value:
                    raise ValidationError("name cannot be empty")

            old_value = getattr(updated, name)
            if old_value == new_value:
                continue
            changed[name] = (old_value, new_value)
            cells[name] = ("Yes" if new_value else "No") if name == "active" else new_value
            updated = replace(updated, **{name: new_value})

        if not changed:
            return item

        self.snapshot.items[item_code] = updated
        self.store.update_by_key(ITEMS_SHEET, {"item_code": item_code}, cells)
        self._log(who, "item", item_code, changed, reason)
        return updated

    def set_renewal_override(
        self, employee_number: str, item_code: str, next_due,
        *, reason: str, authorised_by: str,
    ) -> RenewalOverride:
        """Override a calculated renewal date. Authorisation is mandatory."""
        self.employee(employee_number)
        if item_code not in self.snapshot.items:
            raise ValidationError(f"unknown item code {item_code!r}")
        if not (reason or "").strip():
            raise ValidationError("a reason is required to override a renewal date")
        if not (authorised_by or "").strip():
            raise ValidationError("an override must record who authorised it")

        due = _as_date(next_due)
        if due is None:
            raise ValidationError("a valid renewal date is required")

        previous = self.snapshot.overrides.get((employee_number, item_code))
        override = RenewalOverride(
            employee_number=employee_number, item_code=item_code, next_due=due,
            reason=reason.strip(), authorised_by=authorised_by.strip(),
            set_at=date.today(), active=True,
            row=previous.row if previous else None,
        )
        stored = self.store.upsert_override(override)
        self._log(authorised_by, "renewal override", f"{employee_number}/{item_code}",
                  {"next_due": (previous.next_due if previous else None, due)}, reason)
        return stored

    def clear_renewal_override(self, employee_number: str, item_code: str,
                               *, who: str = "", reason: Optional[str] = None) -> bool:
        """Return an item to its automatically calculated renewal date."""
        previous = self.snapshot.overrides.get((employee_number, item_code))
        if previous is None or not previous.active:
            return False
        self.store.upsert_override(replace(previous, active=False))
        self._log(who, "renewal override", f"{employee_number}/{item_code}",
                  {"active": (True, False)}, reason)
        return True

    def overrides_for(self, employee_number: str) -> dict[str, RenewalOverride]:
        return {
            code: o for (num, code), o in self.snapshot.overrides.items()
            if num == employee_number and o.active
        }

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


def _as_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValidationError(f"{value!r} is not a valid date (use YYYY-MM-DD)") from exc


def _as_employee_status(value):
    from .domain.models import EmployeeStatus

    text = str(value or "").strip().lower().replace(" ", "_")
    try:
        return EmployeeStatus(text)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in EmployeeStatus)
        raise ValidationError(f"status must be one of: {allowed}") from exc


def _renewal_detail(s: ItemStatus) -> str:
    if s.status is UniformStatus.NEVER_ISSUED:
        return "never issued"
    if s.next_due is None:
        return ""
    days = s.days_until_due or 0
    if days < 0:
        return f"due {abs(days)} days ago"
    if days == 0:
        return "due today"
    return f"due in {days} days"


def _next_order_id(when: date, existing: set[str]) -> str:
    """Readable and sortable: ORD-20260818-003."""
    stem = f"ORD-{when:%Y%m%d}"
    n = sum(1 for key in existing if key.startswith(stem)) + 1
    while f"{stem}-{n:03d}" in existing:
        n += 1
    return f"{stem}-{n:03d}"


def _paginate(rows: list, page: int, per_page: int) -> Page:
    per_page = max(1, min(per_page, 500))
    page = max(1, page)
    start = (page - 1) * per_page
    return Page(rows[start : start + per_page], len(rows), page, per_page)


def _order_line(service: "UniformService", order: Order) -> OrderLine:
    snap = service.snapshot
    emp = snap.employees.get(order.employee_number)
    item = snap.items.get(order.item_code)
    delivered = snap.delivered_against(order.order_id, order.employee_number, order.item_code)

    deliveries = [
        i
        for i in snap.issuances.get(order.employee_number, ())
        if i.order_id == order.order_id and i.item_code == order.item_code
    ]
    last = max((i.issued_date for i in deliveries), default=None)
    cycle = next((i.cycle_months for i in deliveries if i.cycle_months), None) or (
        item.renewal_cycle_months if item else None
    )
    return OrderLine(
        order=order,
        employee_name=emp.full_name if emp else order.employee_number,
        role=emp.role if emp else None,
        item_name=item.name if item else order.item_code,
        delivered=delivered,
        status=order_status(
            order.quantity, delivered, order.cancelled,
            measured=order.measured_date is not None,
        ),
        last_delivery=last,
        next_due=add_months(last, cycle) if last and cycle else None,
    )


def _summarise(pr_number: str, lines: list[OrderLine]) -> OrderSummary:
    """Roll a PR's lines up to the order she actually thinks about.

    The order's status is computed from its *totals*, by the same rule a single
    line uses. Taking the least-advanced line instead would read "awaiting
    delivery" for an order the tailor has already part-delivered, which is both
    wrong and the opposite of reassuring.
    """
    lines.sort(key=lambda l: (l.employee_name, l.item_name))
    live = [l for l in lines if not l.order.cancelled]
    ordered = sum(l.order.quantity for l in live)
    delivered = sum(l.delivered for l in live)
    measured = next((l.order.measured_date for l in lines if l.order.measured_date), None)
    return OrderSummary(
        pr_number=pr_number,
        raised=min(l.order.ordered_date for l in lines),
        measured=measured,
        tailor=next((l.order.supplier_ref for l in lines if l.order.supplier_ref), None),
        notes=next((l.order.notes for l in lines if l.order.notes), None),
        lines=tuple(lines),
        status=order_status(
            ordered, delivered, cancelled=not live, measured=measured is not None
        ),
    )
