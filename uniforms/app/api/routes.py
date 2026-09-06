"""JSON API.

A peer of the HTML pages, not a layer beneath them — both call the service
directly. Every list endpoint paginates, because at 10,000 employees an unbounded
response is a mistake waiting to happen.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from ..deps import get_ledger, get_runner, get_service, get_store
from ..domain.models import ORDER_LABELS, Employee, Issuance, ItemStatus, OrderLine, OrderStatus, OrderSummary
from ..service import NotFound, ValidationError

router = APIRouter(prefix="/api", tags=["api"])


def _actor(request: Request, payload: dict) -> str:
    """Who to attribute an edit to.

    The signed-in identity always wins over anything in the request body: with
    sign-in on, the audit trail must record who actually did it, not who the
    caller says did it. Middleware puts the user on request.state.
    """
    user = getattr(request.state, "user", None)
    if user is not None:
        return user.name
    return str(payload.get("who") or payload.get("authorised_by") or "").strip()


# ------------------------------------------------------------------ serialisers

def employee_json(e: Employee) -> dict:
    return {
        "employee_number": e.employee_number,
        "full_name": e.full_name,
        "email": e.email,
        "manager_email": e.manager_email,
        "department": e.department,
        "role": e.role,
        "join_date": e.join_date.isoformat() if e.join_date else None,
        "status": e.status.value,
        "sizes": e.sizes,
        "problems": list(e.problems),
    }


def status_json(s: ItemStatus) -> dict:
    return {
        "employee_number": s.employee_number,
        "employee_name": s.employee_name,
        "item_code": s.item_code,
        "item_name": s.item_name,
        "quantity": s.quantity,
        "status": s.status.value,
        "cycle_months": s.cycle_months,
        "last_issued": s.last_issued.isoformat() if s.last_issued else None,
        "next_due": s.next_due.isoformat() if s.next_due else None,
        "days_until_due": s.days_until_due,
        "size": s.size,
        "reason": s.reason,
    }


def summary_json(s: OrderSummary) -> dict:
    """A whole PR. The figures are rolled up from its lines, never stored."""
    return {
        "pr_number": s.pr_number,
        "raised": s.raised.isoformat(),
        "measured": s.measured.isoformat() if s.measured else None,
        "tailor": s.tailor,
        "notes": s.notes,
        "status": s.status.value,
        "status_label": ORDER_LABELS[s.status],
        "people": s.people,
        "ordered": s.ordered,
        "delivered": s.delivered,
        "outstanding": s.outstanding,
        "first_delivery": s.first_delivery.isoformat() if s.first_delivery else None,
        "last_delivery": s.last_delivery.isoformat() if s.last_delivery else None,
        "lines": [order_json(l) for l in s.lines],
    }


def order_json(line: OrderLine) -> dict:
    o = line.order
    return {
        "pr_number": o.order_id,
        "order_id": o.order_id,
        "employee_number": o.employee_number,
        "employee_name": line.employee_name,
        "role": line.role,
        "item_code": o.item_code,
        "item_name": line.item_name,
        "ordered_date": o.ordered_date.isoformat(),
        "quantity": o.quantity,
        "delivered": line.delivered,
        "pending": line.pending,
        "status": line.status.value,
        "status_label": ORDER_LABELS[line.status],
        "size": o.size,
        "measured_date": o.measured_date.isoformat() if o.measured_date else None,
        "tailor": o.supplier_ref,
        "supplier_ref": o.supplier_ref,
        "notes": o.notes,
        "last_delivery": line.last_delivery.isoformat() if line.last_delivery else None,
        "renewal_due": line.next_due.isoformat() if line.next_due else None,
    }


def issuance_json(i: Issuance) -> dict:
    return {
        "employee_number": i.employee_number,
        "item_code": i.item_code,
        "issued_date": i.issued_date.isoformat(),
        "quantity": i.quantity,
        "size": i.size,
        "issued_by": i.issued_by,
        "cycle_months": i.cycle_months,
        "order_id": i.order_id,
        "notes": i.notes,
    }


# ---------------------------------------------------------------------- routes

@router.get("/employees")
def list_employees(
    q: Optional[str] = None,
    department: Optional[str] = None,
    role: Optional[str] = None,
    active_only: bool = False,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
) -> dict:
    page_obj = get_service().employees(
        q=q, department=department, role=role,
        active_only=active_only, page=page, per_page=per_page,
    )
    return page_obj.as_dict(employee_json)


@router.get("/employees/{employee_number}")
def get_employee(employee_number: str) -> dict:
    service = get_service()
    try:
        employee = service.employee(employee_number)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "employee": employee_json(employee),
        "uniforms": [status_json(s) for s in service.statuses_for(employee_number)],
        "issuances": [
            issuance_json(i)
            for i in sorted(
                service.snapshot.issuances_for(employee_number),
                key=lambda i: i.issued_date,
                reverse=True,
            )
        ],
    }


@router.get("/employees/{employee_number}/uniforms")
def employee_uniforms(employee_number: str) -> dict:
    try:
        return {"items": [status_json(s) for s in get_service().statuses_for(employee_number)]}
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/items")
def list_items() -> dict:
    items = get_service().snapshot.items.values()
    return {
        "items": [
            {
                "item_code": i.item_code,
                "name": i.name,
                "category": i.category,
                "renewal_cycle_months": i.renewal_cycle_months,
                "default_quantity": i.default_quantity,
                "size_key": i.size_key,
                "active": i.active,
            }
            for i in items
        ]
    }


@router.get("/entitlements")
def list_entitlements(role: Optional[str] = None) -> dict:
    snap = get_service().snapshot
    if role:
        rows = snap.entitlements_for(role)
    else:
        rows = [e for group in snap.entitlements.values() for e in group]
    return {
        "entitlements": [
            {"role": e.role, "item_code": e.item_code, "quantity": e.quantity} for e in rows
        ]
    }


@router.get("/status")
def status_report(
    state: Optional[str] = Query(None, description="overdue | due | due_soon | never_issued | ok"),
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
) -> dict:
    try:
        page_obj = get_service().report(
            state, department=department, page=page, per_page=per_page
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return page_obj.as_dict(status_json)


@router.get("/dashboard/summary")
def dashboard_summary() -> dict:
    service = get_service()
    store = get_store()
    return {
        "counts": {**service.summary(), "pending_delivery": service.pending_delivery(),
                   "open_orders": len(service.order_lines(open_only=True))},
        "workbook": {
            "path": str(store.path),
            "loaded_at": store.snapshot.loaded_at.isoformat() if store.snapshot.loaded_at else None,
            "pending_writes": store.pending_writes,
            "last_write_error": store.last_write_error,
            **store.snapshot.counts,
        },
        "reminders": get_ledger().counts(),
        "staff_by_category": service.staff_by_category(),
        "recently_delivered": [order_json(l) for l in service.recently_delivered()],
        "partially_delivered": [
            order_json(l) for l in service.order_lines(status="partially_delivered")
        ],
    }


@router.post("/issuances", status_code=201)
def create_issuance(payload: dict = Body(...)) -> dict:
    service = get_service()
    try:
        issuance = service.record_issuance(
            payload["employee_number"],
            payload["item_code"],
            issued_date=_date(payload.get("issued_date")),
            quantity=payload.get("quantity"),
            size=payload.get("size"),
            issued_by=payload.get("issued_by"),
            notes=payload.get("notes"),
        )
    except KeyError as exc:
        raise HTTPException(400, f"missing field: {exc.args[0]}") from exc
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return issuance_json(issuance)


@router.post("/issuances/bulk", status_code=201)
def create_bulk(payload: dict = Body(...)) -> dict:
    try:
        created = get_service().record_bulk(
            payload.get("employee_numbers") or [],
            payload.get("item_codes") or [],
            issued_date=_date(payload.get("issued_date")),
            issued_by=payload.get("issued_by"),
            notes=payload.get("notes"),
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"created": len(created), "issuances": [issuance_json(i) for i in created]}


@router.get("/issuances")
def list_issuances(
    employee: Optional[str] = None,
    item: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
) -> dict:
    snap = get_service().snapshot
    if employee:
        rows = snap.issuances_for(employee)
    else:
        rows = [i for group in snap.issuances.values() for i in group]
    if item:
        rows = [i for i in rows if i.item_code == item]
    rows.sort(key=lambda i: i.issued_date, reverse=True)
    start = (page - 1) * per_page
    return {
        "items": [issuance_json(i) for i in rows[start : start + per_page]],
        "total": len(rows),
        "page": page,
        "per_page": per_page,
        "pages": max(1, -(-len(rows) // per_page)),
    }


@router.get("/orders")
def list_orders(
    status: Optional[str] = Query(None, description="awaiting_delivery | partially_delivered | delivered"),
    item: Optional[str] = None,
    employee: Optional[str] = None,
    open_only: bool = False,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
) -> dict:
    try:
        rows = get_service().orders(status=status)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    if open_only:
        rows = [r for r in rows if r.is_open]
    if employee:
        rows = [r for r in rows
                if any(l.order.employee_number == employee for l in r.lines)]
    if item:
        rows = [r for r in rows if any(l.order.item_code == item for l in r.lines)]
    start = (page - 1) * per_page
    return {
        "items": [summary_json(r) for r in rows[start : start + per_page]],
        "total": len(rows), "page": page, "per_page": per_page,
        "pages": max(1, -(-len(rows) // per_page)),
        "pending_pieces": get_service().pending_delivery(),
    }


@router.get("/orders/{pr_number}")
def get_order(pr_number: str) -> dict:
    """One PR: its dates, where it has got to, and every line on it."""
    try:
        return summary_json(get_service().order(pr_number))
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/orders", status_code=201)
def create_order(payload: dict = Body(...)) -> dict:
    """Raise one order against a PR number. Lines may cover many people."""
    service = get_service()
    try:
        summary = service.place_order(
            payload["pr_number"],
            payload.get("lines") or [],
            ordered_date=_date(payload.get("ordered_date")),
            tailor=payload.get("tailor"),
            notes=payload.get("notes"),
        )
    except KeyError as exc:
        raise HTTPException(400, f"missing field: {exc.args[0]}") from exc
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"created": len(summary.lines), "order": summary_json(summary)}


@router.post("/orders/{pr_number}/measurement", status_code=201)
def record_measurement(pr_number: str, request: Request, payload: dict = Body(default={})) -> dict:
    """The tailor's measurement visit. One date covers the whole PR."""
    try:
        summary = get_service().record_measurement(
            pr_number,
            _date(payload.get("measured_date")),
            who=_actor(request, payload),
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return summary_json(summary)


@router.post("/orders/{pr_number}/deliveries", status_code=201)
def receive_delivery(pr_number: str, payload: dict = Body(default={})) -> dict:
    """Book in what the tailor brought. This is the handover — it starts the clock.

    ``parts`` is a list of ``{employee_number, item_code, quantity}``; one visit
    normally covers many garments and several people.
    """
    service = get_service()
    try:
        issuances = service.receive_deliveries(
            pr_number,
            payload.get("parts") or [],
            received_date=_date(payload.get("received_date")),
            received_by=payload.get("received_by"),
            notes=payload.get("notes"),
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "delivered": sum(i.quantity for i in issuances),
        "issuances": [issuance_json(i) for i in issuances],
        "order": summary_json(service.order(pr_number)),
    }


@router.get("/action-needed")
def action_needed(limit: int = Query(12, ge=1, le=100)) -> dict:
    rows = get_service().action_needed(limit=limit)
    return {"items": [{**r, "date": r["date"].isoformat() if r["date"] else None} for r in rows]}


@router.patch("/employees/{employee_number}")
def edit_employee(employee_number: str, request: Request, payload: dict = Body(...)) -> dict:
    """Edit staff details. Every change is written to the audit trail."""
    fields = {k: v for k, v in payload.items() if k not in ("who", "reason")}
    try:
        updated = get_service().edit_employee(
            employee_number, fields,
            who=_actor(request, payload), reason=payload.get("reason"),
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return employee_json(updated)


@router.patch("/orders/{pr_number}/{employee_number}/{item_code}")
def edit_order(pr_number: str, employee_number: str, item_code: str,
               request: Request, payload: dict = Body(...)) -> dict:
    """Correct one line of one order. A PR alone does not identify a line."""
    fields = {k: v for k, v in payload.items() if k not in ("who", "reason")}
    try:
        line = get_service().edit_order(
            pr_number, employee_number, item_code, fields,
            who=_actor(request, payload), reason=payload.get("reason")
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return order_json(line)


@router.get("/orders/{pr_number}/deliveries")
def order_deliveries(pr_number: str) -> dict:
    """Each part-delivery with its own date — what a partial order is made of."""
    service = get_service()
    try:
        service.order(pr_number)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"items": [issuance_json(i) for i in service.deliveries_for_order(pr_number)]}


@router.patch("/employees/{employee_number}/deliveries/{row}")
def edit_delivery(employee_number: str, row: int, request: Request, payload: dict = Body(...)) -> dict:
    fields = {k: v for k, v in payload.items() if k not in ("who", "reason")}
    try:
        updated = get_service().edit_delivery(
            employee_number, row, fields,
            who=_actor(request, payload), reason=payload.get("reason"),
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return issuance_json(updated)


@router.patch("/items/{item_code}")
def edit_item(item_code: str, request: Request, payload: dict = Body(...)) -> dict:
    """Edit the catalogue, including the renewal period."""
    fields = {k: v for k, v in payload.items() if k not in ("who", "reason")}
    try:
        updated = get_service().edit_item(
            item_code, fields, who=_actor(request, payload), reason=payload.get("reason")
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "item_code": updated.item_code, "name": updated.name,
        "renewal_cycle_months": updated.renewal_cycle_months,
        "default_quantity": updated.default_quantity, "active": updated.active,
    }


@router.put("/employees/{employee_number}/overrides/{item_code}")
def set_override(employee_number: str, item_code: str, request: Request, payload: dict = Body(...)) -> dict:
    """Override a calculated renewal date. Reason and authoriser are required."""
    try:
        override = get_service().set_renewal_override(
            employee_number, item_code, payload.get("next_due"),
            reason=payload.get("reason", ""),
            authorised_by=_actor(request, payload),
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "employee_number": override.employee_number, "item_code": override.item_code,
        "next_due": override.next_due.isoformat() if override.next_due else None,
        "reason": override.reason, "authorised_by": override.authorised_by,
        "set_at": override.set_at.isoformat(),
    }


@router.delete("/employees/{employee_number}/overrides/{item_code}")
def clear_override(employee_number: str, item_code: str, request: Request, who: str = "") -> dict:
    actor = _actor(request, {"who": who})
    return {"cleared": get_service().clear_renewal_override(employee_number, item_code, who=actor)}


@router.get("/data-quality")
def data_quality() -> dict:
    problems = get_service().data_quality()
    return {"total": len(problems), "problems": problems}


@router.post("/reminders/run")
def run_reminders(dry_run: bool = True) -> dict:
    return get_runner().run(dry_run=dry_run).as_dict()


@router.get("/reminders")
def reminder_history(employee: Optional[str] = None, limit: int = Query(200, le=1000)) -> dict:
    rows = get_ledger().history(employee, limit)
    return {"counts": get_ledger().counts(), "items": [dict(r) for r in rows]}


@router.post("/workbook/reload")
def reload_workbook() -> dict:
    store = get_store()
    changed = store.reload_if_changed()
    return {"reloaded": changed, **store.snapshot.counts}


@router.post("/workbook/flush")
def flush_workbook() -> dict:
    return {"written": get_store().flush()}


def _date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(400, f"issued_date must be ISO format (YYYY-MM-DD), got {value!r}") from exc
