"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .capture.routes import router as capture_router
from .dashboard.routes import router as dashboard_router
from .db import init_db
from .discovery.routes import router as discovery_router
from .email.routes import router as email_router
from .jobs import shutdown_scheduler, start_scheduler
from .outreach.routes import router as outreach_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title="Lead Generation System", lifespan=lifespan)

app.include_router(dashboard_router)
app.include_router(discovery_router)
app.include_router(capture_router)
app.include_router(outreach_router)
app.include_router(email_router)
