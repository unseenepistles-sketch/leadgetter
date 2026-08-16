"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router as api_router
from .config import get_settings
from .deps import get_store
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
    if settings.reminders_enabled and not settings.reminders_dry_run:
        log.warning("LIVE REMINDER SENDING IS ON — real email will go to real people")
    yield
    shutdown_scheduler()


app = FastAPI(title="Uniform Management System", lifespan=lifespan)
app.include_router(ui_router)
app.include_router(api_router)
