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
from ..domain.models import Employee, Issuance, ItemStatus
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


def issuance_json(i: Issuance) -> dict:
    return {
        "employee_number": i.employee_number,
        "item_code": i.item_code,
        "issued_date": i.issued_date.isoformat(),
        "quantity": i.quantity,
        "size": i.size,
        "issued_by": i.issued_by,
        "cycle_months": i.cycle_months,
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
        "counts": service.summary(),
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
