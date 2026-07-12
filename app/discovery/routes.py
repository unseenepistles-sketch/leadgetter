"""Discovery routes: run a search, and browse discovered creators."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import get_session
from ..models import Creator, Niche
from ..web import templates
from .service import run_discovery

router = APIRouter()


class DiscoverIn(BaseModel):
    seed: str
    location: str | None = None
    platforms: list[str] = ["youtube"]
    per_term: int = 5
    top_n: int = 10


@router.post("/api/discover")
def api_discover(payload: DiscoverIn, session: Session = Depends(get_session)) -> dict:
    return run_discovery(
        session,
        seed=payload.seed,
        location=payload.location,
        platforms=tuple(payload.platforms) or ("youtube",),
        per_term=payload.per_term,
        top_n=payload.top_n,
    )


@router.post("/discover")
def discover_form(
    seed: str = Form(...),
    location: str = Form(""),
    platforms: list[str] = Form(default=["youtube"]),
    per_term: int = Form(5),
    top_n: int = Form(10),
    session: Session = Depends(get_session),
):
    run_discovery(
        session,
        seed=seed,
        location=location or None,
        platforms=tuple(platforms) or ("youtube",),
        per_term=per_term,
        top_n=top_n,
    )
    return RedirectResponse("/creators", status_code=303)


@router.get("/creators")
def creators(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(Creator).order_by(Creator.followers.desc())).all()
    niches = {n.id: n for n in session.exec(select(Niche)).all()}
    return templates.TemplateResponse(
        request,
        "creators.html",
        {"request": request, "creators": rows, "niches": niches},
    )
