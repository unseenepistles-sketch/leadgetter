"""FastAPI application — dashboard, discovery, outreach, opt-in, campaigns, exports."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import state
from .ai import llm
from .config import get_settings
from .database import get_session, init_db
from .models import Campaign, Creator
from .routes_api import router as api_router
from .services import ads, campaigns, creators, leads, outreach, sheets

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="LeadSystem", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["comma"] = lambda n: f"{int(n):,}" if n not in (None, "") else "0"
app.include_router(api_router)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


# ── Dashboard ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def console(request: Request):
    """The single-page operator console. It loads its data from /api/bootstrap."""
    return templates.TemplateResponse(request, "console.html", {})


@app.post("/settings")
def save_settings(offer: str = Form(""), sender_name: str = Form(""),
                  lead_magnet_url: str = Form(""), welcome_body: str = Form("")):
    state.save(offer=offer, sender_name=sender_name,
               lead_magnet_url=lead_magnet_url, welcome_body=welcome_body)
    return RedirectResponse("/?msg=Settings+saved", status_code=303)


# ── Creator discovery + outreach ───────────────────────────────────────────

@app.post("/discover")
def discover(db: Session = Depends(get_session), niche: str = Form(...),
             location: str = Form(""), platform: str = Form("instagram"),
             limit: int = Form(25)):
    state.save(niche=niche, location=location, platform=platform)
    found, source = creators.discover(niche, location, platform, limit)
    saved = creators.save_creators(db, found, niche)
    try:
        sheets.append_creators(saved)
    except Exception:  # noqa: BLE001
        pass
    msg = f"Found {len(saved)} creators ({source} data)"
    return RedirectResponse(f"/?msg={msg.replace(' ', '+')}", status_code=303)


@app.post("/outreach/{creator_id}")
def make_outreach(creator_id: int, db: Session = Depends(get_session)):
    creator = db.get(Creator, creator_id)
    if not creator:
        return RedirectResponse("/?msg=Creator+not+found", status_code=303)
    st = state.load()
    offer = st.get("offer") or "my product"
    outreach.draft_and_store(db, creator, offer, st.get("sender_name", ""))
    return RedirectResponse(f"/?msg=Outreach+drafted+for+@{creator.handle}", status_code=303)


# ── Opt-in landing page + lead capture ─────────────────────────────────────

@app.get("/landing", response_class=HTMLResponse)
def landing(request: Request):
    st = state.load()
    return templates.TemplateResponse(request, "landing.html", {
        "state": st,
        "offer": st.get("offer") or "our free guide",
    })


@app.post("/subscribe")
def subscribe(request: Request, db: Session = Depends(get_session),
              email: str = Form(...), name: str = Form(""),
              consent: str = Form(""), source: str = Form("landing")):
    st = state.load()
    lead, message, created = leads.capture(
        db, email=email, name=name, niche=st.get("niche", ""),
        source=source, consent=consent in {"on", "true", "1", "yes"},
        ip=_client_ip(request),
    )
    ok = lead is not None
    if ok and created:
        # Deliver the freebie immediately. Best-effort — never break the opt-in.
        try:
            campaigns.send_welcome(db, lead, st.get("offer", ""),
                                   st.get("lead_magnet_url", ""), st.get("welcome_body", ""))
        except Exception:  # noqa: BLE001
            pass
    return templates.TemplateResponse(request, "thanks.html", {
        "ok": ok, "message": message,
    }, status_code=200 if ok else 400)


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
def do_unsubscribe(token: str, request: Request, db: Session = Depends(get_session)):
    ok = leads.unsubscribe(db, token)
    return templates.TemplateResponse(request, "thanks.html", {
        "ok": ok,
        "message": "You've been unsubscribed. Sorry to see you go."
        if ok else "That unsubscribe link wasn't recognised.",
    })


# ── Campaigns ──────────────────────────────────────────────────────────────

@app.post("/campaigns/draft")
def campaign_draft(db: Session = Depends(get_session), name: str = Form(...),
                   goal: str = Form("")):
    st = state.load()
    offer = st.get("offer") or "my offer"
    subject, body = campaigns.draft(st.get("niche", ""), offer, goal)
    campaigns.create(db, name=name, niche=st.get("niche", ""), subject=subject, body=body)
    return RedirectResponse("/?msg=Campaign+drafted", status_code=303)


@app.post("/campaigns/{campaign_id}/send")
def campaign_send(campaign_id: int, db: Session = Depends(get_session)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        return RedirectResponse("/?msg=Campaign+not+found", status_code=303)
    result = campaigns.send(db, campaign)
    mode = "sent" if result["live"] else "dry-run"
    msg = f"Campaign {mode}: {result['sent']}/{result['recipients']} delivered"
    return RedirectResponse(f"/?msg={msg.replace(' ', '+')}", status_code=303)


# ── Exports + health ───────────────────────────────────────────────────────

@app.get("/export/audience.csv")
def export_audience(db: Session = Depends(get_session)):
    st = state.load()
    csv_text = ads.audience_csv(db, st.get("niche") or None)
    return Response(csv_text, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=ad_audience_targeting.csv",
    })


@app.get("/health")
def health():
    settings = get_settings()
    return {
        "app": "ok",
        "ai": llm.health(),
        "discovery_live": settings.discovery_live,
        "email_live": settings.email_live,
        "sheets_enabled": settings.sheets_enabled,
    }
