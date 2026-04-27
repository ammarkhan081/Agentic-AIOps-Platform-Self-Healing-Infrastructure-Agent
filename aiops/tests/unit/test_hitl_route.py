"""
Unit tests for HITL route submission behavior.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from types import SimpleNamespace

from fastapi import HTTPException

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

hitl_route = importlib.import_module("src.api.routes.hitl")


def test_submit_hitl_decision_rejects_missing_incident():
    try:
        asyncio.run(
            hitl_route.submit_hitl_decision(
                "missing",
                hitl_route.HITLDecisionRequest(decision="approve"),
                user={"username": "admin", "role": "admin"},
            )
        )
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_submit_hitl_decision_abort_path(monkeypatch):
    incident_id = "inc-route-hitl-1"
    hitl_route._incidents[incident_id] = {
        "status": "paused",
        "state": {"incident_id": incident_id, "status": "paused", "retry_count": 1},
    }
    persisted = []
    events = []
    monkeypatch.setattr(hitl_route, "_cancel_hitl_watchdog", lambda iid: None)
    monkeypatch.setattr(
        hitl_route, "_persist_snapshot", lambda iid, status, state: persisted.append((iid, status))
    )
    monkeypatch.setattr(
        hitl_route, "_broadcast", lambda iid, event: events.append((iid, event["type"]))
    )
    monkeypatch.setattr(hitl_route, "_persist_hitl_event", lambda iid, actor, action, payload: None)

    result = asyncio.run(
        hitl_route.submit_hitl_decision(
            incident_id,
            hitl_route.HITLDecisionRequest(decision="abort", reason="unsafe"),
            user={"username": "admin", "role": "admin"},
        )
    )

    assert result["status"] == "escalated"
    assert persisted[-1] == (incident_id, "escalated")
    assert events[-1][1] == "pipeline_complete"
    hitl_route._incidents.pop(incident_id, None)


def test_submit_hitl_decision_rejects_non_paused_incident():
    incident_id = "inc-route-hitl-2"
    hitl_route._incidents[incident_id] = {
        "status": "active",
        "state": {"incident_id": incident_id, "status": "active"},
    }

    try:
        asyncio.run(
            hitl_route.submit_hitl_decision(
                incident_id,
                hitl_route.HITLDecisionRequest(decision="approve"),
                user={"username": "admin", "role": "admin"},
            )
        )
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 409
    finally:
        hitl_route._incidents.pop(incident_id, None)


def test_submit_hitl_decision_repauses_and_restarts_watchdog(monkeypatch):
    incident_id = "inc-route-hitl-3"
    hitl_route._incidents[incident_id] = {
        "status": "paused",
        "state": {
            "incident_id": incident_id,
            "status": "paused",
            "retry_count": 1,
            "alert": SimpleNamespace(service="order-service", severity="HIGH"),
        },
    }
    events = []
    restarted = []

    class FakeGraph:
        def stream(self, state, config=None, stream_mode=None):
            yield {**state, "status": "paused", "hitl_required": True}

    monkeypatch.setattr(hitl_route, "get_graph", lambda: FakeGraph())
    monkeypatch.setattr(hitl_route, "_cancel_hitl_watchdog", lambda iid: None)
    monkeypatch.setattr(hitl_route, "_persist_snapshot", lambda iid, status, state: None)
    monkeypatch.setattr(hitl_route, "_broadcast", lambda iid, event: events.append(event["type"]))
    monkeypatch.setattr(hitl_route, "_persist_hitl_event", lambda iid, actor, action, payload: None)
    monkeypatch.setattr(
        hitl_route, "_start_hitl_watchdog", lambda iid, actor: restarted.append((iid, actor))
    )
    monkeypatch.setattr(hitl_route, "observe_hitl_intervention", lambda: None)

    result = asyncio.run(
        hitl_route.submit_hitl_decision(
            incident_id,
            hitl_route.HITLDecisionRequest(decision="approve"),
            user={"username": "ammar", "role": "sre"},
        )
    )

    assert result["status"] == "paused"
    assert restarted == [(incident_id, "ammar")]
    assert "hitl_required" in events
    hitl_route._incidents.pop(incident_id, None)


def test_submit_hitl_decision_handles_graph_failure(monkeypatch):
    incident_id = "inc-route-hitl-4"
    hitl_route._incidents[incident_id] = {
        "status": "paused",
        "state": {"incident_id": incident_id, "status": "paused"},
    }
    events = []

    class FakeGraph:
        def stream(self, state, config=None, stream_mode=None):
            raise RuntimeError("resume failed")
            yield state

    monkeypatch.setattr(hitl_route, "get_graph", lambda: FakeGraph())
    monkeypatch.setattr(hitl_route, "_cancel_hitl_watchdog", lambda iid: None)
    monkeypatch.setattr(hitl_route, "_persist_snapshot", lambda iid, status, state: None)
    monkeypatch.setattr(hitl_route, "_broadcast", lambda iid, event: events.append(event["type"]))
    monkeypatch.setattr(hitl_route, "_persist_hitl_event", lambda iid, actor, action, payload: None)

    result = asyncio.run(
        hitl_route.submit_hitl_decision(
            incident_id,
            hitl_route.HITLDecisionRequest(decision="approve"),
            user={"username": "admin", "role": "admin"},
        )
    )

    assert "Graph resume failed" in result["message"]
    assert "pipeline_error" in events
    hitl_route._incidents.pop(incident_id, None)
