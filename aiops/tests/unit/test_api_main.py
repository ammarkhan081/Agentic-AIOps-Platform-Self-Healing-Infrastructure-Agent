"""
Unit tests for API app wiring and lifespan startup behavior.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from types import SimpleNamespace

try:
    import prometheus_client  # type: ignore  # noqa: F401
except ModuleNotFoundError:

    class _FakeMetric:
        def __init__(self, *args, **kwargs):
            self._value = SimpleNamespace(get=lambda: 0.0)

        def inc(self, *args, **kwargs):
            return None

        def set(self, *args, **kwargs):
            return None

    sys.modules["prometheus_client"] = SimpleNamespace(
        CONTENT_TYPE_LATEST="text/plain",
        Counter=_FakeMetric,
        Gauge=_FakeMetric,
        generate_latest=lambda: b"",
    )

api_main = importlib.import_module("src.api.main")


async def _run_lifespan(module):
    async with module.lifespan(module.app):
        return None


def test_create_app_exposes_expected_docs_and_routes():
    app = api_main.create_app()
    paths = {route.path for route in app.router.routes}

    assert app.docs_url == "/api/v1/docs"
    assert app.redoc_url == "/api/v1/redoc"
    assert app.openapi_url == "/api/v1/openapi.json"
    assert "/api/v1/health" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/incidents" in paths
    assert "/api/v1/reports" in paths


def test_lifespan_runs_startup_seeders(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(api_main, "create_tables", lambda: calls.append("tables"))
    monkeypatch.setattr(api_main.auth, "seed_default_users", lambda: calls.append("users"))
    monkeypatch.setattr(api_main, "seed_synthetic_incidents", lambda: calls.append("memory"))

    asyncio.run(_run_lifespan(api_main))

    assert calls == ["tables", "users", "memory"]
    assert api_main.app.state.startup_status["database_initialized"] is True
    assert api_main.app.state.startup_status["incident_memory_seeded"] is True


def test_lifespan_tolerates_startup_failures(monkeypatch):
    warnings: list[str] = []

    monkeypatch.setattr(
        api_main, "create_tables", lambda: (_ for _ in ()).throw(RuntimeError("db"))
    )
    monkeypatch.setattr(api_main.auth, "seed_default_users", lambda: warnings.append("unexpected"))
    monkeypatch.setattr(
        api_main,
        "seed_synthetic_incidents",
        lambda: (_ for _ in ()).throw(RuntimeError("incident memory")),
    )
    monkeypatch.setattr(
        api_main.logger, "warning", lambda message, *args: warnings.append(message % args)
    )

    asyncio.run(_run_lifespan(api_main))

    assert any("Database initialization failed" in warning for warning in warnings)
    assert any("Incident-memory seed failed" in warning for warning in warnings)
    assert api_main.app.state.startup_status["database_initialized"] is False
    assert api_main.app.state.startup_status["incident_memory_seeded"] is False
