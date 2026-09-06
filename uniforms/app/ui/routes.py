"""Server-rendered pages.

These call the service directly rather than going back through the JSON API, so
the two stay peers and the business logic lives in exactly one place.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import COOKIE, User
from ..config import get_settings
from ..deps import get_auth, get_ledger, get_runner, get_service, get_store
from ..domain.models import ORDER_LABELS, EmployeeStatus, OrderStatus, UniformStatus
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


def current_user(request: Request) -> Optional[User]:
    return get_auth().read(request.cookies.get(COOKIE))


def _actor(request: Request, form) -> str:
    """Who to record for an edit.

    A signed-in identity always wins over the form field: when sign-in is on,
    "authorised by" should be a fact rather than a claim.
    """
    user = current_user(request)
    if user is not None:
        return user.name
    return (form.get("who") or form.get("authorised_by") or "").strip()


def _guard(request: Request, *, admin: bool = False):
    """Returns a redirect when the request may not proceed, else None."""
    auth = get_auth()
    if not auth.enabled:
        return None
    user = current_user(request)
    if user is None:
        return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
    if admin and not user.admin:
        return _back("/", err="You do not have permission to change records.")
    return None


def _render(request: Request, template: str, active: str, **context):
    return templates.TemplateResponse(
        request, template,
        {
            "brand": get_settings().brand_name,
            "active": active,
            "user": current_user(request),
            "auth_on": get_auth().enabled,
            **context,
        },
    )


@router.get("/login")
def login_form(request: Request, next: str = "/"):
    if not get_auth().enabled:
        return _back("/", ok="Sign-in is not switched on for this installation.")
    return _render(request, "login.html", "", next=next)


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    target = form.get("next") or "/"
    user = get_auth().authenticate(form.get("username", ""), form.get("password", ""))
    if user is None:
        log.warning("failed sign-in for %r", form.get("username"))
        return _back("/login", err="That username and password did not match.")
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        COOKIE, get_auth().issue(user),
        httponly=True, samesite="lax",
        secure=request.url.scheme == "https", max_age=get_auth().max_age_seconds,
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


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
        by_category=service.staff_by_category(),
        recently=service.recently_delivered(limit=6),
        partial=service.order_lines(status="partially_delivered")[:6],
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
        overrides=service.overrides_for(employee_number),
        orders=service.order_lines(employee_number=employee_number),
        today=date.today().isoformat(),
    )


@router.get("/employees/{employee_number}/edit")
def edit_employee_form(request: Request, employee_number: str):
    service = get_service()
    try:
        employee = service.employee(employee_number)
    except NotFound:
        return _back("/employees", err=f"No employee {employee_number}")
    return _render(
        request, "edit_employee.html", "employees",
        employee=employee, roles=service.roles(), departments=service.departments(),
        statuses=[s.value for s in EmployeeStatus],
    )


@router.post("/employees/{employee_number}/edit")
async def edit_employee_submit(employee_number: str, request: Request):
    form = await request.form()
    fields = {
        k: (form.get(k) or None)
        for k in ("full_name", "email", "manager_email", "department", "role",
                  "join_date", "status", "shirt_size", "trouser_size",
                  "blazer_size", "shoe_size")
        if k in form
    }
    try:
        get_service().edit_employee(
            employee_number, fields,
            who=_actor(request, form), reason=form.get("reason") or None,
        )
    except (ValidationError, NotFound) as exc:
        return _back(f"/employees/{employee_number}/edit", err=str(exc))
    return _back(f"/employees/{employee_number}", ok="Staff details updated.")


@router.post("/employees/{employee_number}/override")
async def set_override(employee_number: str, request: Request):
    form = await request.form()
    item_code = form.get("item_code") or ""
    try:
        if form.get("clear"):
            get_service().clear_renewal_override(
                employee_number, item_code, who=_actor(request, form)
            )
            message = "Renewal date returned to the calculated one."
        else:
            get_service().set_renewal_override(
                employee_number, item_code, form.get("next_due"),
                reason=form.get("reason") or "",
                authorised_by=_actor(request, form),
            )
            message = "Renewal date overridden and recorded in the audit trail."
    except (ValidationError, NotFound) as exc:
        return _back(f"/employees/{employee_number}", err=str(exc))
    return _back(f"/employees/{employee_number}", ok=message)


@router.get("/catalogue")
def catalogue(request: Request):
    service = get_service()
    return _render(request, "catalogue.html", "catalogue",
                   items=list(service.snapshot.items.values()))


@router.post("/catalogue/{item_code}")
async def edit_item_submit(item_code: str, request: Request):
    form = await request.form()
    fields = {}
    if form.get("renewal_cycle_months"):
        fields["renewal_cycle_months"] = form["renewal_cycle_months"]
    if form.get("default_quantity"):
        fields["default_quantity"] = form["default_quantity"]
    try:
        get_service().edit_item(item_code, fields, who=_actor(request, form),
                                reason=form.get("reason") or None)
    except (ValidationError, NotFound) as exc:
        return _back("/catalogue", err=str(exc))
    return _back("/catalogue", ok=f"{item_code} updated. Existing issues keep their old cycle.")


@router.post("/orders/{pr_number}/{employee_number}/{item_code}/edit")
async def edit_order_submit(pr_number: str, employee_number: str, item_code: str,
                            request: Request):
    form = await request.form()
    fields = {}
    if form.get("quantity"):
        fields["quantity"] = form["quantity"]
    if form.get("ordered_date"):
        fields["ordered_date"] = form["ordered_date"]
    if "notes" in form:
        fields["notes"] = form.get("notes") or None
    if form.get("cancelled"):
        fields["cancelled"] = True
    try:
        get_service().edit_order(pr_number, employee_number, item_code, fields,
                                 who=_actor(request, form),
                                 reason=form.get("reason") or None)
    except (ValidationError, NotFound) as exc:
        return _back("/orders", err=str(exc))
    return _back("/orders", ok=f"{pr_number} updated.")


@router.post("/orders/{pr_number}/measurement")
async def record_measurement_submit(pr_number: str, request: Request):
    """The tailor came and took sizes. One visit, one date, the whole PR."""
    form = await request.form()
    when = None
    if form.get("measured_date"):
        try:
            when = date.fromisoformat(form["measured_date"])
        except ValueError:
            return _back("/orders", err=f"{form['measured_date']!r} is not a valid date")
    try:
        get_service().record_measurement(pr_number, when, who=_actor(request, form))
    except (ValidationError, NotFound) as exc:
        return _back("/orders", err=str(exc))
    return _back("/orders", ok=f"Measurement visit recorded for {pr_number}. "
                              "Now awaiting delivery.")


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
def orders(request: Request, status: str = "all"):
    """The order log, one row per PR — the level she works at."""
    service = get_service()
    try:
        rows = service.orders(status=status)
    except ValidationError:
        status, rows = "all", service.orders()
    return _render(
        request, "orders.html", "orders",
        orders=rows, status=status, statuses=ORDER_STATES,
        pending=service.pending_delivery(), today=date.today().isoformat(),
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
    if not (form.get("pr_number") or "").strip():
        return _back("/orders/new", err="A PR number is required.")
    # A bulk order covers many people under one PR, so the form posts a checkbox
    # per person rather than a single staff picker.
    employee_numbers = [v.strip() for v in form.getlist("employee_number") if v.strip()]
    if not employee_numbers:
        return _back("/orders/new", err="Tick at least one member of staff.")

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
        lines = [
            {"employee_number": e, "item_code": code, "quantity": qty}
            for e in employee_numbers
            for code, qty in quantities.items()
            if qty
        ]
        summary = get_service().place_order(
            form.get("pr_number") or "", lines, ordered_date=when,
            tailor=form.get("tailor") or None,
            notes=form.get("notes") or None,
        )
    except (ValidationError, NotFound) as exc:
        return _back("/orders/new", err=str(exc))

    return _back(
        "/orders",
        ok=f"{summary.pr_number} raised — {summary.ordered} item(s) for {summary.people} staff. "
           "Awaiting the tailor's measurement visit.",
    )


@router.post("/orders/{pr_number}/{employee_number}/{item_code}/deliver")
async def deliver_order(pr_number: str, employee_number: str, item_code: str, request: Request):
    form = await request.form()
    try:
        qty = int(form.get("quantity") or 0) or None
    except ValueError:
        qty = None
    try:
        get_service().receive_delivery(
            pr_number, employee_number, item_code,
            quantity=qty, received_by=form.get("received_by"),
        )
    except (ValidationError, NotFound) as exc:
        return _back("/orders", err=str(exc))
    return _back("/orders", ok="Delivery booked in. The renewal clock starts from today.")


@router.get("/orders/{pr_number}")
def order_detail(request: Request, pr_number: str):
    """One PR: its journey, and every line grouped by the person it is for."""
    service = get_service()
    try:
        summary = service.order(pr_number)
    except NotFound:
        return _back("/orders", err=f"No order {pr_number}.")

    grouped: dict[str, dict] = {}
    for line in summary.lines:
        person = grouped.setdefault(
            line.order.employee_number,
            {"employee_number": line.order.employee_number, "name": line.employee_name,
             "role": line.role, "lines": [], "ordered": 0, "delivered": 0},
        )
        person["lines"].append(line)
        person["ordered"] += line.order.quantity
        person["delivered"] += line.delivered
    for person in grouped.values():
        person["outstanding"] = max(0, person["ordered"] - person["delivered"])

    return _render(
        request, "order.html", "orders",
        order=summary, people=sorted(grouped.values(), key=lambda p: p["name"]),
        today=date.today().isoformat(),
    )


@router.get("/orders/{pr_number}/edit")
def edit_order_form(request: Request, pr_number: str):
    service = get_service()
    try:
        summary = service.order(pr_number)
    except NotFound:
        return _back("/orders", err=f"No order {pr_number}.")
    return _render(request, "edit_order.html", "orders",
                   order=summary, today=date.today().isoformat())


@router.post("/orders/{pr_number}/edit")
async def edit_order_header_submit(pr_number: str, request: Request):
    """The order itself: tailor, remarks, and the two dates."""
    form = await request.form()
    fields = {
        "supplier_ref": form.get("tailor") or None,
        "notes": form.get("notes") or None,
        "ordered_date": form.get("ordered_date") or None,
        "measured_date": form.get("measured_date") or None,
    }
    try:
        get_service().edit_order_header(
            pr_number, fields, who=_actor(request, form),
            reason=form.get("reason") or None,
        )
    except (ValidationError, NotFound) as exc:
        return _back(f"/orders/{pr_number}/edit", err=str(exc))
    return _back(f"/orders/{pr_number}", ok=f"{pr_number} updated.")


#: How long a promised-but-undelivered garment may sit before it needs chasing.
CHASE_DAYS = 30


@router.get("/pending")
def pending(request: Request):
    """The chase list: what the tailor still owes, and how long it has been owed."""
    rows = get_service().outstanding_lines()
    return _render(
        request, "pending.html", "pending",
        rows=rows, pieces=sum(r["outstanding"] for r in rows),
        chasing=[r for r in rows if r["waiting_days"] >= CHASE_DAYS],
        chase_days=CHASE_DAYS,
    )


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
