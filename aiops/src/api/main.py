"""
ASHIA AIOps Control Plane FastAPI application.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from ..core.config import get_settings
from ..core.logging import configure_logging, reset_request_id, set_request_id
from ..db.models import create_tables
from ..tools.chroma_tool import seed_synthetic_incidents
from .routes import auth, health, hitl, incidents, reports

configure_logging()
logger = logging.getLogger("ashia-api")


def _startup_status_defaults() -> dict[str, bool]:
    return {
        "database_initialized": False,
        "incident_memory_seeded": False,
        "monitor_loop_started": False,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ASHIA AIOps API starting up")
    app.state.startup_status = _startup_status_defaults()
    try:
        create_tables()
        auth.ensure_user_schema()
        auth.seed_default_users()
        app.state.startup_status["database_initialized"] = True
    except Exception as exc:
        logger.warning("Database initialization failed (non-fatal): %s", exc)
    try:
        seed_synthetic_incidents()
        app.state.startup_status["incident_memory_seeded"] = True
    except Exception as exc:
        logger.warning("Incident-memory seed failed (non-fatal): %s", exc)
    await incidents.start_automatic_monitor_loop()
    app.state.startup_status["monitor_loop_started"] = True
    yield
    await incidents.stop_automatic_monitor_loop()
    app.state.startup_status["monitor_loop_started"] = False
    logger.info("ASHIA AIOps API shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ASHIA - Agentic AIOps Self-Healing Infrastructure Agent",
        description=(
            "Multi-agent autonomous platform for infrastructure fault detection, "
            "root cause analysis, remediation, and continual learning."
        ),
        version="1.0.0",
        docs_url="/api/v1/docs",
        redoc_url="/api/v1/redoc",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    app.state.startup_status = _startup_status_defaults()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/livez", include_in_schema=False)
    async def livez():
        return {"status": "alive"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        startup_status = getattr(app.state, "startup_status", _startup_status_defaults())
        ready = (
            startup_status.get("database_initialized", False)
            and startup_status.get("incident_memory_seeded", False)
            and startup_status.get("monitor_loop_started", False)
        )
        return {
            "status": "ready" if ready else "degraded",
            "checks": startup_status,
        }

    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(incidents.router, prefix="/api/v1/incidents", tags=["incidents"])
    app.include_router(hitl.router, prefix="/api/v1/incidents", tags=["hitl"])
    app.include_router(reports.router, prefix="/api/v1/reports", tags=["reports"])

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("APP_PORT", "8080")),
        reload=True,
        log_config=None,
    )
