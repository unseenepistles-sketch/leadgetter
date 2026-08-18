"""JSON API.

A peer of the HTML pages, not a layer beneath them — both call the service
directly. Every list endpoint paginates, because at 10,000 employees an unbounded
response is a mistake waiting to happen.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query

from ..deps import get_ledger, get_runner, get_service, get_store
from ..domain.models import ORDER_LABELS, Employee, Issuance, ItemStatus, OrderLine, OrderStatus
from ..service import NotFound, ValidationError

router = APIRouter(prefix="/api", tags=["api"])


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


def order_json(line: OrderLine) -> dict:
    o = line.order
    return {
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
        rows = get_service().order_lines(
            status=status, item_code=item, employee_number=employee, open_only=open_only
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    start = (page - 1) * per_page
    return {
        "items": [order_json(r) for r in rows[start : start + per_page]],
        "total": len(rows), "page": page, "per_page": per_page,
        "pages": max(1, -(-len(rows) // per_page)),
        "pending_pieces": get_service().pending_delivery(),
    }


@router.get("/orders/{order_id}")
def get_order(order_id: str) -> dict:
    try:
        return order_json(get_service().order_line(order_id))
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/orders", status_code=201)
def create_order(payload: dict = Body(...)) -> dict:
    """Place one order covering several items: 6 shirts and 3 trousers in one go."""
    service = get_service()
    try:
        created = service.place_order(
            payload["employee_number"],
            payload.get("quantities") or {},
            ordered_date=_date(payload.get("ordered_date")),
            supplier_ref=payload.get("supplier_ref"),
            notes=payload.get("notes"),
        )
    except KeyError as exc:
        raise HTTPException(400, f"missing field: {exc.args[0]}") from exc
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "created": len(created),
        "orders": [order_json(service.order_line(o.order_id)) for o in created],
    }


@router.post("/orders/{order_id}/deliveries", status_code=201)
def receive_delivery(order_id: str, payload: dict = Body(default={})) -> dict:
    """Book in what arrived. This is the handover — it starts the renewal clock."""
    service = get_service()
    try:
        issuance = service.receive_delivery(
            order_id,
            quantity=payload.get("quantity"),
            received_date=_date(payload.get("received_date")),
            size=payload.get("size"),
            received_by=payload.get("received_by"),
            notes=payload.get("notes"),
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"issuance": issuance_json(issuance), "order": order_json(service.order_line(order_id))}


@router.get("/action-needed")
def action_needed(limit: int = Query(12, ge=1, le=100)) -> dict:
    rows = get_service().action_needed(limit=limit)
    return {"items": [{**r, "date": r["date"].isoformat() if r["date"] else None} for r in rows]}


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
