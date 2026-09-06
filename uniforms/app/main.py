"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .api.routes import router as api_router
from .auth import COOKIE
from .config import get_settings
from .deps import get_auth, get_store
from .jobs import shutdown_scheduler, start_scheduler
from .ui.routes import router as ui_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("uniforms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Parse the workbook once. At 10,000 employees this takes a few seconds, and
    # it is the reason every request afterwards is served from memory.
    get_store().load()
    start_scheduler()
    log.info("capabilities: %s", settings.capabilities())
    yield
    shutdown_scheduler()


app = FastAPI(title="Uniform Management System", lifespan=lifespan)

#: Reachable without signing in.
OPEN_PATHS = {"/login", "/logout", "/healthz", "/docs", "/openapi.json", "/redoc"}
#: Methods that change records, and so require an admin.
WRITING = {"POST", "PATCH", "PUT", "DELETE"}


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    """One gate in front of everything.

    Deliberately middleware rather than a per-route dependency: a new endpoint
    added later is protected by default instead of being protected only if
    somebody remembers to decorate it.
    """
    auth = get_auth()
    if not auth.enabled or request.url.path in OPEN_PATHS:
        return await call_next(request)

    user = auth.read(request.cookies.get(COOKIE))
    wants_json = request.url.path.startswith("/api")

    if user is None:
        if wants_json:
            return JSONResponse({"detail": "sign-in required"}, status_code=401)
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    if request.method in WRITING and not user.admin:
        message = "your account cannot change records"
        if wants_json:
            return JSONResponse({"detail": message}, status_code=403)
        return RedirectResponse(f"/?err={message}", status_code=303)

    # Handlers read this to attribute edits to the signed-in user rather than
    # trusting a name typed into a form.
    request.state.user = user
    return await call_next(request)


app.include_router(ui_router)
app.include_router(api_router)
