"""
Unit tests for incident route read and export behavior.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status

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

incidents = importlib.import_module("src.api.routes.incidents")


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self._offset = 0
        self._limit = None

    def filter(self, *args, **kwargs):
        self.filters.append((args, kwargs))
        return self

    def order_by(self, *args, **kwargs):
        return self

    def count(self):
        return len(self.rows)

    def offset(self, value):
        self._offset = value
        return self

    def limit(self, value):
        self._limit = value
        return self

    def all(self):
        rows = self.rows[self._offset :]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows


class FakeDB:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def get(self, _model, incident_id):
        return self.row

    def query(self, _model):
        return FakeQuery(self.rows)


def build_record():
    return SimpleNamespace(
        incident_id="inc-12345678",
        status="resolved",
        state_snapshot={
            "alert": {
                "service": "order-service",
                "severity": "CRITICAL",
                "description": "memory leak",
            },
            "hypotheses": [
                {
                    "description": "memory leak",
                    "confidence": 0.9,
                    "suggested_fix_category": "restart",
                    "attempted": True,
                }
            ],
            "execution_log": [
                {
                    "executed_at": "2026-03-30T10:00:00",
                    "action_type": "restart_container",
                    "outcome": "success",
                    "duration_seconds": 10,
                }
            ],
            "postmortem": {
                "incident_id": "inc-12345678",
                "service": "order-service",
                "outcome": "resolved",
                "time_to_recovery_seconds": 45.0,
                "retry_count": 1,
                "total_cost_usd": 0.02,
                "created_at": "2026-03-30T10:05:00",
                "root_cause_confirmed": "memory leak",
                "fix_applied": "restart_container",
            },
        },
    )


def test_list_incidents_falls_back_to_in_memory_cache():
    incidents._incidents["inc-memory"] = {
        "status": "active",
        "state": {
            "created_at": "2026-03-30T10:00:00",
            "retry_count": 0,
            "alert": SimpleNamespace(service="order-service", severity="HIGH"),
        },
    }

    class ExplodingDB:
        def query(self, _model):
            raise RuntimeError("db unavailable")

    result = asyncio.run(incidents.list_incidents(user={"role": "viewer"}, db=ExplodingDB()))

    assert result["incidents"][0]["incident_id"] == "inc-memory"
    assert result["pagination"]["total"] == 1
    incidents._incidents.pop("inc-memory", None)


def test_get_incident_uses_record_and_events(monkeypatch):
    monkeypatch.setattr(
        incidents, "list_audit_events", lambda db, incident_id: [{"type": "pipeline_started"}]
    )

    result = asyncio.run(
        incidents.get_incident(
            "inc-12345678",
            user={"role": "viewer"},
            db=FakeDB(row=build_record()),
        )
    )

    assert result["status"] == "resolved"
    assert result["postmortem"]["fix_applied"] == "restart_container"
    assert result["events"][0]["type"] == "pipeline_started"
    assert result["total_cost_usd"] == 0.0


def test_get_incident_postmortem_returns_payload():
    result = asyncio.run(
        incidents.get_incident_postmortem(
            "inc-12345678",
            user={"role": "viewer"},
            db=FakeDB(row=build_record()),
        )
    )

    assert result["root_cause_confirmed"] == "memory leak"


def test_export_incident_postmortem_markdown(monkeypatch):
    monkeypatch.setattr(
        incidents,
        "list_audit_events",
        lambda db, incident_id: [{"timestamp": "2026-03-30T10:00:00", "type": "pipeline_started"}],
    )

    response = asyncio.run(
        incidents.export_incident_postmortem(
            "inc-12345678",
            format="markdown",
            user={"role": "viewer"},
            db=FakeDB(row=build_record()),
        )
    )

    body = response.body.decode()
    assert "Incident Postmortem" in body
    assert "Execution Log" in body


def test_export_incident_postmortem_json():
    result = asyncio.run(
        incidents.export_incident_postmortem(
            "inc-12345678",
            format="json",
            user={"role": "viewer"},
            db=FakeDB(row=build_record()),
        )
    )

    assert result["service"] == "order-service"


def test_get_incident_raises_when_missing():
    try:
        asyncio.run(incidents.get_incident("missing", user={"role": "viewer"}, db=FakeDB()))
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_trigger_incident_queues_pipeline():
    class FakeBackground:
        def __init__(self):
            self.calls = []

        def add_task(self, fn, *args):
            self.calls.append((fn, args))

    background = FakeBackground()

    result = asyncio.run(
        incidents.trigger_incident(
            incidents.TriggerRequest(service="order-service", notes="manual run"),
            background,
            user={"username": "ammar", "role": "sre"},
        )
    )

    assert result["status"] == "initiated"
    assert result["stream_url"].endswith("/stream")
    assert background.calls[0][0] is incidents._run_pipeline


def test_list_incidents_uses_db_filters():
    row = SimpleNamespace(
        incident_id="inc-1",
        status="resolved",
        service="order-service",
        severity="CRITICAL",
        created_at=datetime(2026, 3, 30, 12, 0, 0),
        resolved_at=None,
        time_to_recovery=12.0,
        retry_count=1,
        total_cost_usd=0.02,
    )
    query = FakeQuery([row])

    class FilterDB:
        def query(self, _model):
            return query

    result = asyncio.run(
        incidents.list_incidents(
            status="resolved",
            severity="CRITICAL",
            service="order-service",
            date_from="2026-03-30T00:00:00",
            date_to="2026-03-31T00:00:00",
            user={"role": "viewer"},
            db=FilterDB(),
        )
    )

    assert result["incidents"][0]["incident_id"] == "inc-1"
    assert len(query.filters) >= 5
    assert result["pagination"]["total"] == 1


def test_list_incidents_rejects_bad_iso_date():
    incidents._incidents["inc-bad-date"] = {
        "status": "active",
        "state": {"created_at": "2026-03-30T10:00:00", "retry_count": 0},
    }

    result = asyncio.run(
        incidents.list_incidents(
            date_from="not-a-date",
            user={"role": "viewer"},
            db=FakeDB(rows=[]),
        )
    )

    assert result["incidents"][0]["incident_id"] == "inc-bad-date"
    incidents._incidents.pop("inc-bad-date", None)


def test_get_incident_uses_in_memory_snapshot_when_db_missing():
    incidents._incidents["inc-live"] = {
        "status": "paused",
        "state": {
            "retry_count": 2,
            "hitl_required": True,
            "alert": SimpleNamespace(service="order-service", severity="HIGH"),
        },
    }

    result = asyncio.run(incidents.get_incident("inc-live", user={"role": "viewer"}, db=FakeDB()))

    assert result["status"] == "paused"
    assert result["retry_count"] == 2
    incidents._incidents.pop("inc-live", None)


def test_get_incident_postmortem_raises_when_missing():
    row = SimpleNamespace(incident_id="inc-1", state_snapshot={})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            incidents.get_incident_postmortem("inc-1", user={"role": "viewer"}, db=FakeDB(row=row))
        )

    assert exc.value.status_code == 404


def test_export_incident_postmortem_pdf(monkeypatch):
    monkeypatch.setattr(incidents, "list_audit_events", lambda db, incident_id: [])
    real_import = __import__

    class FakeCanvas:
        def __init__(self, buffer, pagesize=None):
            self.buffer = buffer

        def drawString(self, x, y, text):
            self.buffer.write(text.encode("utf-8"))

        def showPage(self):
            return None

        def save(self):
            self.buffer.write(b"pdf")

    def fake_import(name, *args, **kwargs):
        if name == "reportlab.lib.pagesizes":
            return SimpleNamespace(letter=(612, 792))
        if name == "reportlab.pdfgen":
            return SimpleNamespace(canvas=SimpleNamespace(Canvas=FakeCanvas))
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    response = asyncio.run(
        incidents.export_incident_postmortem(
            "inc-12345678",
            format="pdf",
            user={"role": "viewer"},
            db=FakeDB(row=build_record()),
        )
    )

    assert response.media_type == "application/pdf"


def test_export_incident_postmortem_pdf_unavailable(monkeypatch):
    monkeypatch.setattr(incidents, "list_audit_events", lambda db, incident_id: [])
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("reportlab"):
            raise RuntimeError("missing reportlab")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            incidents.export_incident_postmortem(
                "inc-12345678",
                format="pdf",
                user={"role": "viewer"},
                db=FakeDB(row=build_record()),
            )
        )

    assert exc.value.status_code == 501


def test_broadcast_removes_dead_clients(monkeypatch):
    sent = []

    async def ok_send(event):
        sent.append(event["type"])

    async def bad_send(event):
        raise RuntimeError("gone")

    incidents._ws_clients["inc-broadcast"] = [
        SimpleNamespace(send_json=ok_send),
        SimpleNamespace(send_json=bad_send),
    ]

    monkeypatch.setattr(incidents.asyncio, "create_task", lambda coro: asyncio.run(coro))

    incidents._broadcast("inc-broadcast", {"type": "state_update"})

    assert sent == ["state_update"]
    assert len(incidents._ws_clients["inc-broadcast"]) == 1
    incidents._ws_clients.pop("inc-broadcast", None)


def test_cancel_hitl_watchdog_cancels_active_task():
    cancelled = []

    class FakeTask:
        def done(self):
            return False

        def cancel(self):
            cancelled.append(True)

    incidents._hitl_watchdogs["inc-watch"] = FakeTask()

    incidents._cancel_hitl_watchdog("inc-watch")

    assert cancelled == [True]
    assert "inc-watch" not in incidents._hitl_watchdogs


def test_start_hitl_watchdog_replaces_existing(monkeypatch):
    cancelled = []
    created = []

    class FakeTask:
        def done(self):
            return False

        def cancel(self):
            cancelled.append(True)

    incidents._hitl_watchdogs["inc-watch2"] = FakeTask()
    monkeypatch.setattr(
        incidents.asyncio, "create_task", lambda coro: created.append(coro) or "task"
    )

    incidents._start_hitl_watchdog("inc-watch2", "ammar")

    assert cancelled == [True]
    assert incidents._hitl_watchdogs["inc-watch2"] == "task"
    created[0].close()
    incidents._hitl_watchdogs.pop("inc-watch2", None)


def test_persist_snapshot_and_event_handle_success():
    closed = []
    db = SimpleNamespace(close=lambda: closed.append(True))

    class FakeFactory:
        def __call__(self):
            return db

    original_snapshot = incidents.upsert_incident_record
    original_event = incidents.append_audit_event
    calls = []
    incidents.upsert_incident_record = lambda current_db, incident_id, state, status: calls.append(
        ("snapshot", incident_id, status)
    )
    incidents.append_audit_event = lambda current_db, incident_id, action, actor, details: (
        calls.append(("event", incident_id, action, actor))
    )
    incidents.get_session_factory = lambda: FakeFactory()
    try:
        incidents._persist_snapshot("inc-1", "active", {"status": "active"})
        incidents._persist_event("inc-1", "state_update", "system", {"x": 1})
    finally:
        incidents.upsert_incident_record = original_snapshot
        incidents.append_audit_event = original_event

    assert ("snapshot", "inc-1", "active") in calls
    assert ("event", "inc-1", "state_update", "system") in calls
    assert closed == [True, True]


def test_persist_snapshot_and_event_swallow_failures(monkeypatch):
    monkeypatch.setattr(
        incidents, "get_session_factory", lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    )

    incidents._persist_snapshot("inc-1", "active", {})
    incidents._persist_event("inc-1", "state_update", "system", {})


def test_run_pipeline_resolved(monkeypatch):
    incident_id = "inc-pipeline-ok"
    persisted = []
    events = []
    resolved = []

    alert = SimpleNamespace(service="order-service", severity="CRITICAL")

    class FakeGraph:
        def stream(self, state, config=None, stream_mode=None):
            yield {
                **state,
                "status": "active",
                "alert": alert,
                "hypotheses": [1],
                "retry_count": 0,
                "hitl_required": False,
            }
            yield {
                **state,
                "status": "resolved",
                "alert": alert,
                "time_to_recovery": 33.0,
                "retry_count": 1,
            }

    monkeypatch.setattr(incidents, "get_graph", lambda: FakeGraph())
    monkeypatch.setattr(
        incidents, "_persist_snapshot", lambda iid, status, state: persisted.append((iid, status))
    )
    monkeypatch.setattr(
        incidents,
        "_persist_event",
        lambda iid, event_type, actor, details: events.append((event_type, details)),
    )
    monkeypatch.setattr(incidents, "_broadcast", lambda iid, event: None)
    monkeypatch.setattr(incidents, "_cancel_hitl_watchdog", lambda iid: None)
    monkeypatch.setattr(
        incidents, "observe_incident_detected", lambda: events.append(("detected", {}))
    )
    monkeypatch.setattr(incidents, "observe_incident_resolved", lambda ttr: resolved.append(ttr))

    asyncio.run(
        incidents._run_pipeline(
            incident_id, "ammar", incidents.TriggerRequest(service="order-service")
        )
    )

    assert incidents._incidents[incident_id]["status"] == "resolved"
    complete = [details for event_type, details in events if event_type == "pipeline_complete"][0]
    assert complete["status"] == "resolved"
    assert complete["time_to_recovery"] == 33.0
    assert complete["retry_count"] == 1
    assert resolved == [33.0]
    incidents._incidents.pop(incident_id, None)


def test_run_pipeline_pauses_for_hitl(monkeypatch):
    incident_id = "inc-pipeline-pause"
    events = []
    started = []
    hitl_metric = []
    alert = SimpleNamespace(service="order-service", severity="HIGH")

    class FakeGraph:
        def stream(self, state, config=None, stream_mode=None):
            yield {
                **state,
                "status": "paused",
                "alert": alert,
                "hypotheses": [1, 2],
                "retry_count": 1,
                "hitl_required": True,
            }

    monkeypatch.setattr(incidents, "get_graph", lambda: FakeGraph())
    monkeypatch.setattr(incidents, "_persist_snapshot", lambda iid, status, state: None)
    monkeypatch.setattr(
        incidents,
        "_persist_event",
        lambda iid, event_type, actor, details: events.append(event_type),
    )
    monkeypatch.setattr(incidents, "_broadcast", lambda iid, event: None)
    monkeypatch.setattr(
        incidents, "_start_hitl_watchdog", lambda iid, actor: started.append((iid, actor))
    )
    monkeypatch.setattr(incidents, "observe_incident_detected", lambda: None)
    monkeypatch.setattr(incidents, "observe_hitl_intervention", lambda: hitl_metric.append(True))

    asyncio.run(incidents._run_pipeline(incident_id, "ammar", incidents.TriggerRequest()))

    assert incidents._incidents[incident_id]["status"] == "paused"
    assert started == [(incident_id, "ammar")]
    assert hitl_metric == [True]
    assert "hitl_required" in events
    incidents._incidents.pop(incident_id, None)


def test_run_pipeline_failure(monkeypatch):
    incident_id = "inc-pipeline-fail"
    persisted = []
    events = []

    class FakeGraph:
        def stream(self, state, config=None, stream_mode=None):
            raise RuntimeError("graph broke")
            yield state

    monkeypatch.setattr(incidents, "get_graph", lambda: FakeGraph())
    monkeypatch.setattr(
        incidents,
        "_persist_snapshot",
        lambda iid, status, state: persisted.append((iid, status, state.get("status"))),
    )
    monkeypatch.setattr(
        incidents,
        "_persist_event",
        lambda iid, event_type, actor, details: events.append((event_type, details)),
    )
    monkeypatch.setattr(incidents, "_broadcast", lambda iid, event: None)
    monkeypatch.setattr(incidents, "_cancel_hitl_watchdog", lambda iid: None)
    monkeypatch.setattr(incidents, "observe_incident_detected", lambda: None)

    asyncio.run(incidents._run_pipeline(incident_id, "ammar", incidents.TriggerRequest()))

    assert incidents._incidents[incident_id]["status"] == "failed"
    assert persisted[-1][1] == "failed"
    assert events[-1][0] == "pipeline_error"
    incidents._incidents.pop(incident_id, None)


def test_stream_incident_replays_events_and_cleans_up(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.sent = []
            self.accepted = False
            self.pings = 0
            self.closed = None

        async def accept(self):
            self.accepted = True

        async def receive_json(self):
            return {"type": "auth", "token": "valid-token"}

        async def send_json(self, payload):
            self.sent.append(payload)
            if payload.get("type") == "ping":
                raise incidents.WebSocketDisconnect()

        async def close(self, code=None, reason=None):
            self.closed = {"code": code, "reason": reason}

    class FakeDB:
        def close(self):
            return None

    socket = FakeSocket()
    incidents._ws_clients.pop("inc-stream", None)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(
        incidents,
        "list_audit_events",
        lambda db, incident_id: [{"type": "pipeline_started", "incident_id": incident_id}],
    )
    monkeypatch.setattr(incidents, "get_session_factory", lambda: lambda: FakeDB())
    monkeypatch.setattr(
        incidents,
        "get_current_user_from_token",
        lambda token, db: {"username": "viewer", "role": "viewer"},
    )
    monkeypatch.setattr(incidents.asyncio, "sleep", lambda seconds: real_sleep(0))

    asyncio.run(incidents.stream_incident("inc-stream", socket))

    assert socket.accepted is True
    assert socket.sent[0]["type"] == "pipeline_started"
    assert "inc-stream" in incidents._ws_clients
    assert socket not in incidents._ws_clients["inc-stream"]


def test_stream_incident_handles_replay_failure(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.sent = []
            self.closed = None

        async def accept(self):
            return None

        async def receive_json(self):
            return {"type": "auth", "token": "valid-token"}

        async def send_json(self, payload):
            self.sent.append(payload)
            raise incidents.WebSocketDisconnect()

        async def close(self, code=None, reason=None):
            self.closed = {"code": code, "reason": reason}

    socket = FakeSocket()
    incidents._ws_clients.pop("inc-stream-fail", None)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(
        incidents, "get_session_factory", lambda: lambda: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(
        incidents,
        "get_current_user_from_token",
        lambda token, db: {"username": "viewer", "role": "viewer"},
    )
    monkeypatch.setattr(
        incidents,
        "list_audit_events",
        lambda db, incident_id: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    monkeypatch.setattr(incidents.asyncio, "sleep", lambda seconds: real_sleep(0))

    asyncio.run(incidents.stream_incident("inc-stream-fail", socket))

    assert socket.sent[0]["type"] == "ping"


def test_stream_incident_rejects_missing_token():
    class FakeSocket:
        def __init__(self):
            self.closed = None
            self.accepted = False

        async def accept(self):
            self.accepted = True

        async def receive_json(self):
            return {}

        async def close(self, code=None, reason=None):
            self.closed = {"code": code, "reason": reason}

    socket = FakeSocket()

    asyncio.run(incidents.stream_incident("inc-no-token", socket))

    assert socket.closed["code"] == status.WS_1008_POLICY_VIOLATION
