"""Server-rendered pages.

These call the service directly rather than going back through the JSON API, so
the two stay peers and the business logic lives in exactly one place.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import get_settings
from ..deps import get_ledger, get_runner, get_service, get_store
from ..domain.models import ORDER_LABELS, OrderStatus, UniformStatus
from ..service import NotFound, ValidationError

log = logging.getLogger("uniforms.ui")

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

STATES = [
    ("all", "All"),
    ("never_issued", "Never issued"),
    ("overdue", "Overdue"),
    ("due", "Due now"),
    ("due_soon", "Due within 6 months"),
    ("ok", "Up to date"),
    ("needs_review", "Needs review"),
]


def _render(request: Request, template: str, active: str, **context):
    return templates.TemplateResponse(
        request, template,
        {"brand": get_settings().brand_name, "active": active, **context},
    )


def _back(url: str, *, ok: str = "", err: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("ok", ok), ("err", err)) if v}
    target = f"{url}?{urlencode(params)}" if params else url
    return RedirectResponse(target, status_code=303)


@router.get("/")
def dashboard(request: Request):
    service, store, settings = get_service(), get_store(), get_settings()
    return _render(
        request, "dashboard.html", "dashboard",
        counts=service.summary(),
        store=store.snapshot,
        store_path=str(store.path),
        pending_writes=store.pending_writes,
        last_write_error=store.last_write_error,
        reminder_counts=get_ledger().counts(),
        live=settings.sending_for_real,
        pending_delivery=service.pending_delivery(),
        open_orders=len(service.order_lines(open_only=True)),
        actions=service.action_needed(limit=8),
    )


@router.get("/employees")
def employees(
    request: Request,
    q: Optional[str] = None,
    department: Optional[str] = None,
    active_only: bool = False,
    page: int = 1,
):
    service = get_service()
    result = service.employees(
        q=q, department=department, active_only=active_only, page=page, per_page=50
    )
    # Only compute outstanding counts for the rows on screen — doing it for all
    # 10,000 employees on every page view would be pointless work.
    outstanding = {
        e.employee_number: sum(
            1 for s in service.statuses_for(e.employee_number) if s.is_outstanding
        )
        for e in result.items
    }
    query = urlencode({k: v for k, v in (
        ("q", q), ("department", department), ("active_only", "1" if active_only else "")
    ) if v})
    return _render(
        request, "employees.html", "employees",
        page=result, q=q, department=department, active_only=active_only,
        departments=service.departments(), outstanding=outstanding,
        base_url=f"/employees?{query}&" if query else "/employees?",
    )


@router.get("/employees/{employee_number}")
def employee_detail(request: Request, employee_number: str):
    service = get_service()
    try:
        employee = service.employee(employee_number)
    except NotFound:
        return _back("/employees", err=f"No employee {employee_number}")
    return _render(
        request, "employee.html", "employees",
        employee=employee,
        statuses=service.statuses_for(employee_number),
        issuances=sorted(
            service.snapshot.issuances_for(employee_number),
            key=lambda i: i.issued_date, reverse=True,
        ),
        today=date.today().isoformat(),
    )


@router.get("/issue")
def issue_form(request: Request):
    service = get_service()
    return _render(
        request, "issue.html", "issue",
        items=[i for i in service.snapshot.items.values() if i.active],
        employees=sorted(
            (e for e in service.snapshot.employees.values() if e.is_active),
            key=lambda e: e.full_name.lower(),
        ),
        today=date.today().isoformat(),
    )


@router.post("/issue")
def issue_submit(
    employee_number: Optional[str] = Form(None),
    employee_numbers: list[str] = Form(default=[]),
    item_codes: list[str] = Form(default=[]),
    issued_date: Optional[str] = Form(None),
    issued_by: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    service = get_service()
    targets = [employee_number] if employee_number else list(employee_numbers)
    back = f"/employees/{employee_number}" if employee_number else "/issue"

    when: Optional[date] = None
    if issued_date:
        try:
            when = date.fromisoformat(issued_date)
        except ValueError:
            return _back(back, err=f"{issued_date!r} is not a valid date")

    try:
        created = service.record_bulk(
            targets, list(item_codes), issued_date=when, issued_by=issued_by, notes=notes
        )
    except (ValidationError, NotFound) as exc:
        return _back(back, err=str(exc))

    people = len({i.employee_number for i in created})
    return _back(
        back,
        ok=f"Recorded {len(created)} item(s) for {people} employee(s). "
           "Saving to the workbook in the background.",
    )


ORDER_STATES = [("all", "All statuses")] + [(s.value, ORDER_LABELS[s]) for s in OrderStatus]


@router.get("/orders")
def orders(request: Request, status: str = "all", item: Optional[str] = None):
    service = get_service()
    try:
        lines = service.order_lines(status=status, item_code=item)
    except ValidationError:
        status, lines = "all", service.order_lines(item_code=item)
    return _render(
        request, "orders.html", "orders",
        lines=lines, status=status, item=item, statuses=ORDER_STATES,
        items=[i for i in service.snapshot.items.values() if i.active],
        pending=service.pending_delivery(),
    )


@router.get("/orders/new")
def new_order_form(request: Request, employee: Optional[str] = None):
    service = get_service()
    return _render(
        request, "new_order.html", "orders",
        employees=sorted(
            (e for e in service.snapshot.employees.values() if e.is_active),
            key=lambda e: e.full_name.lower(),
        ),
        items=[i for i in service.snapshot.items.values() if i.active],
        today=date.today().isoformat(), preselect=employee,
    )


@router.post("/orders")
async def create_order(request: Request):
    form = await request.form()
    employee_number = (form.get("employee_number") or "").strip()
    if not employee_number:
        return _back("/orders/new", err="Choose a staff member first.")

    quantities = {}
    for key, value in form.items():
        if key.startswith("qty_"):
            try:
                n = int(value or 0)
            except ValueError:
                n = 0
            if n > 0:
                quantities[key[4:]] = n

    when = None
    raw = form.get("ordered_date")
    if raw:
        try:
            when = date.fromisoformat(raw)
        except ValueError:
            return _back("/orders/new", err=f"{raw!r} is not a valid date")

    try:
        created = get_service().place_order(
            employee_number, quantities, ordered_date=when,
            supplier_ref=form.get("supplier_ref") or None,
            notes=form.get("notes") or None,
        )
    except (ValidationError, NotFound) as exc:
        return _back("/orders/new", err=str(exc))

    pieces = sum(o.quantity for o in created)
    return _back("/orders", ok=f"Order placed: {pieces} piece(s) across {len(created)} item type(s).")


@router.post("/orders/{order_id}/deliver")
async def deliver_order(order_id: str, request: Request):
    form = await request.form()
    try:
        qty = int(form.get("quantity") or 0) or None
    except ValueError:
        qty = None
    try:
        get_service().receive_delivery(order_id, quantity=qty, received_by=form.get("received_by"))
    except (ValidationError, NotFound) as exc:
        return _back("/orders", err=str(exc))
    return _back("/orders", ok="Delivery booked in. The renewal clock starts from today.")


@router.get("/reports")
def reports(
    request: Request,
    state: str = "overdue",
    department: Optional[str] = None,
    page: int = 1,
):
    service = get_service()
    try:
        result = service.report(state, department=department, page=page, per_page=50)
    except ValidationError:
        state, result = "all", service.report(None, department=department, page=page, per_page=50)

    label = dict(STATES).get(state, "Report")
    query = urlencode({k: v for k, v in (("state", state), ("department", department)) if v})
    return _render(
        request, "reports.html", "reports",
        page=result, state=state, department=department, states=STATES,
        departments=service.departments(), title=label,
        base_url=f"/reports?{query}&" if query else "/reports?",
    )


@router.get("/data-quality")
def data_quality(request: Request):
    return _render(request, "data_quality.html", "dq", problems=get_service().data_quality())


@router.post("/workbook/reload")
def reload_workbook():
    store = get_store()
    try:
        changed = store.reload_if_changed()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
        log.exception("reload failed")
        return _back("/", err=f"Could not reload: {exc}")
    return _back("/", ok="Reloaded from the workbook." if changed else "Already up to date.")


@router.post("/reminders/run")
def run_reminders(dry_run: str = Form("1")):
    result = get_runner().run(dry_run=dry_run == "1")
    note = f" {'; '.join(result.notes)}" if result.notes else ""
    return _back(
        "/",
        ok=f"Scanned {result.scanned}, would notify {result.sent} recipient(s)."
           f"{note}" if result.dry_run else
           f"Sent {result.sent} message(s), {result.failed} failed.{note}",
    )


@router.get("/healthz")
def healthz():
    store = get_store()
    return {
        "ok": True,
        "employees": store.snapshot.counts["employees"],
        "pending_writes": store.pending_writes,
        "last_write_error": store.last_write_error,
    }
