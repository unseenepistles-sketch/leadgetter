"""Partnership-outreach pipeline: track and AI-draft messages to creators.

Sending is manual (you copy the draft and send it yourself, to a public business
contact). Nothing here automates DMs or bulk-messages individuals.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from ..ai import claude
from ..config import get_settings
from ..db import get_session
from ..models import Creator, OutreachTask
from ..web import templates

router = APIRouter()
settings = get_settings()
_STATUSES = ["to_contact", "contacted", "replied", "deal", "passed"]


@router.get("/outreach")
def board(request: Request, session: Session = Depends(get_session)):
    tasks = session.exec(select(OutreachTask)).all()
    creators = {c.id: c for c in session.exec(select(Creator)).all()}
    columns = {s: [] for s in _STATUSES}
    for t in tasks:
        columns.setdefault(t.status, []).append(t)
    return templates.TemplateResponse(
        request,
        "outreach.html",
        {"request": request, "columns": columns, "creators": creators, "statuses": _STATUSES},
    )


@router.post("/outreach/{task_id}/status")
def set_status(
    task_id: int,
    status: str = Form(...),
    session: Session = Depends(get_session),
):
    task = session.get(OutreachTask, task_id)
    if task and status in _STATUSES:
        task.status = status
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
    return RedirectResponse("/outreach", status_code=303)


@router.post("/outreach/{task_id}/draft")
def draft(task_id: int, session: Session = Depends(get_session)):
    task = session.get(OutreachTask, task_id)
    if task:
        creator = session.get(Creator, task.creator_id)
        if creator:
            task.draft_message = claude.draft_outreach(
                creator.display_name or creator.handle,
                creator.platform,
                settings.product,
                settings.brand_name,
            )
            task.updated_at = datetime.utcnow()
            session.add(task)
            session.commit()
    return RedirectResponse("/outreach", status_code=303)
