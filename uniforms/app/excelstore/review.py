"""What changed in the workbook, in words somebody can check.

The client edits this file in Excel. That is the whole point of keeping it as
the system of record — but it means a mistyped row, a sort applied to one column
instead of the sheet, or a stray delete arrives looking exactly like a
deliberate change.

So a change on disk is not adopted on sight. It is parsed into a *candidate*
snapshot, compared against what the app currently holds, and described. Nothing
takes effect until somebody has looked at the description and said yes.

This module is pure: two snapshots in, a description out. No I/O, no state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..domain.models import Employee, Issuance, Order, UniformItem
from .workbook import Snapshot

#: Fields worth mentioning when a record is edited rather than added or removed.
#: Deliberately not every field — `row` moves whenever anyone inserts a line
#: above, and reporting that as a change would bury the real ones.
WATCHED = {
    "employee": ("full_name", "email", "manager_email", "department", "role",
                 "join_date", "status", "sizes"),
    "item": ("name", "category", "renewal_cycle_months", "default_quantity", "active"),
    "order": ("ordered_date", "quantity", "size", "measured_date", "supplier_ref",
              "notes", "cancelled"),
}


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: Any
    after: Any

    def __str__(self) -> str:
        return f"{_label(self.field)}: {_show(self.before)} → {_show(self.after)}"


@dataclass(frozen=True, slots=True)
class RecordChange:
    """One record that was added, removed or edited."""

    kind: str           # "employee" | "item" | "order line" | "delivery"
    key: str            # how a person would refer to it
    action: str         # "added" | "removed" | "edited"
    fields: tuple[FieldChange, ...] = ()

    @property
    def summary(self) -> str:
        if self.action == "edited":
            return "; ".join(str(f) for f in self.fields)
        return ""


@dataclass
class WorkbookDiff:
    changes: list[RecordChange] = field(default_factory=list)
    #: Problems the *candidate* has that the current snapshot does not — rows
    #: that have become unreadable since the last good read.
    new_problems: list[str] = field(default_factory=list)

    def of(self, kind: str, action: Optional[str] = None) -> list[RecordChange]:
        return [c for c in self.changes
                if c.kind == kind and (action is None or c.action == action)]

    @property
    def is_empty(self) -> bool:
        return not self.changes and not self.new_problems

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for change in self.changes:
            out[f"{change.kind}s {change.action}"] = out.get(
                f"{change.kind}s {change.action}", 0) + 1
        return out

    @property
    def removals(self) -> list[RecordChange]:
        """Deletions, which is what a mistake in Excel usually looks like."""
        return [c for c in self.changes if c.action == "removed"]

    def headline(self) -> str:
        """One line she can read without opening anything."""
        if self.is_empty:
            return "No changes."
        parts = [f"{n} {name}" for name, n in sorted(self.counts.items())]
        if self.new_problems:
            parts.append(f"{len(self.new_problems)} rows that cannot be read")
        return " · ".join(parts)


def diff_snapshots(current: Snapshot, candidate: Snapshot) -> WorkbookDiff:
    """Describe what turning ``current`` into ``candidate`` would do."""
    diff = WorkbookDiff()

    _compare(diff, "employee", current.employees, candidate.employees,
             WATCHED["employee"], lambda k, v: f"{v.full_name} ({k})")
    _compare(diff, "item", current.items, candidate.items,
             WATCHED["item"], lambda k, v: f"{v.name} ({k})")
    _compare(diff, "order line", current.orders, candidate.orders,
             WATCHED["order"], _order_label)
    _compare_deliveries(diff, current, candidate)

    # Compared on the message, not the row number. Deleting one row renumbers
    # every row below it, and keying on the number would report every existing
    # problem as brand new — burying the one row that actually broke.
    known = {(p.sheet, p.message) for p in current.problems}
    diff.new_problems = [
        f"{p.sheet} row {p.row}: {p.message}"
        for p in candidate.problems if (p.sheet, p.message) not in known
    ]
    return diff


def _compare(diff, kind, before: dict, after: dict, watched, label) -> None:
    for key in before.keys() - after.keys():
        diff.changes.append(RecordChange(kind, label(key, before[key]), "removed"))
    for key in after.keys() - before.keys():
        diff.changes.append(RecordChange(kind, label(key, after[key]), "added"))
    for key in before.keys() & after.keys():
        fields = tuple(
            FieldChange(f, getattr(before[key], f), getattr(after[key], f))
            for f in watched
            if getattr(before[key], f, None) != getattr(after[key], f, None)
        )
        if fields:
            diff.changes.append(
                RecordChange(kind, label(key, after[key]), "edited", fields))


def _compare_deliveries(diff, current: Snapshot, candidate: Snapshot) -> None:
    """Issuance rows have no natural key, so they are compared as a multiset.

    Two identical handovers on the same day are a real thing — two shirts issued
    to one person in two transactions — so counting them, rather than putting
    them in a set, is what keeps the second one visible.
    """
    before, after = _delivery_counts(current), _delivery_counts(candidate)
    for signature in before.keys() | after.keys():
        delta = after.get(signature, 0) - before.get(signature, 0)
        if not delta:
            continue
        emp, code, when, qty = signature
        name = current.employees.get(emp) or candidate.employees.get(emp)
        label = (f"{name.full_name if name else emp} · {code} · "
                 f"{when:%d %b %Y} × {qty}")
        for _ in range(abs(delta)):
            diff.changes.append(
                RecordChange("delivery", label, "added" if delta > 0 else "removed"))


def _delivery_counts(snap: Snapshot) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    for rows in snap.issuances.values():
        for i in rows:
            key = (i.employee_number, i.item_code, i.issued_date, i.quantity)
            out[key] = out.get(key, 0) + 1
    return out


def _order_label(key, order: Order) -> str:
    pr, emp, code = key
    return f"{pr} · {emp} · {code}"


def _label(field: str) -> str:
    return field.replace("_", " ").capitalize()


def _show(value: Any) -> str:
    if value is None or value == "":
        return "(blank)"
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in sorted(value.items())) or "(none)"
    if hasattr(value, "strftime"):
        return value.strftime("%d %b %Y")
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)
