"""
Unit tests for persistence helpers, control metrics, and graph construction.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
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

from src.core import metrics
from src.db import store
from src.graph import graph as graph_module


@dataclass
class DemoDataclass:
    value: str


def test_serialize_value_handles_nested_dataclasses():
    payload = {"items": [DemoDataclass(value="x")], "plain": "ok"}

    result = store.serialize_value(payload)

    assert result == {"items": [{"value": "x"}], "plain": "ok"}


def test_build_incident_projection_uses_alert_and_fix_data():
    state = {
        "created_at": "2026-03-30T10:00:00",
        "resolved_at": "2026-03-30T10:05:00",
        "alert": {
            "service": "order-service",
            "severity": "CRITICAL",
            "metric_name": "order_memory_leak_bytes",
        },
        "selected_fix": {"fix_id": "f1", "action_type": "restart_container"},
        "hitl_response": {"decision": "approve", "decided_by": "admin"},
        "retry_count": 1,
        "time_to_recovery": 45.0,
        "total_cost_usd": 0.02,
    }

    projection = store.build_incident_projection(state, "resolved")

    assert projection["service"] == "order-service"
    assert projection["severity"] == "CRITICAL"
    assert projection["fix_applied"] == "restart_container"
    assert projection["hitl_decision"] == "approve"
    assert projection["created_at"].isoformat() == "2026-03-30T10:00:00"


def test_build_incident_projection_uses_postmortem_and_defaults():
    state = {
        "postmortem": {
            "service": "user-service",
            "alert_signature": "user-service:user_db_connections:CRITICAL",
            "root_cause_confirmed": "pool exhaustion",
            "fix_applied": "db_connection_reset",
            "outcome": "resolved",
        },
        "hitl_required": True,
    }

    projection = store.build_incident_projection(state, "resolved")

    assert projection["service"] == "user-service"
    assert projection["alert_signature"] == "user-service:user_db_connections:CRITICAL"
    assert projection["root_cause"] == "pool exhaustion"
    assert projection["created_at"] is not None


def test_list_audit_events_formats_rows():
    class FakeQuery:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return self.rows

    class FakeDB:
        def query(self, _model):
            return FakeQuery(
                [
                    SimpleNamespace(
                        action="pipeline_started",
                        incident_id="inc-1",
                        created_at=SimpleNamespace(isoformat=lambda: "2026-03-30T10:00:00"),
                        details={"message": "started"},
                        id=1,
                    )
                ]
            )

    events = store.list_audit_events(FakeDB(), "inc-1")

    assert events[0]["type"] == "pipeline_started"
    assert events[0]["message"] == "started"


def test_upsert_incident_record_creates_and_updates():
    refreshed = []

    class FakeDB:
        def __init__(self):
            self.record = None
            self.added = []

        def get(self, _model, incident_id):
            return self.record

        def add(self, record):
            self.record = record
            self.added.append(record)

        def commit(self):
            return None

        def refresh(self, record):
            refreshed.append(record)

    db = FakeDB()
    state = {
        "created_at": "2026-03-30T10:00:00",
        "alert": {
            "service": "order-service",
            "severity": "HIGH",
            "metric_name": "order_error_rate",
        },
    }

    created = store.upsert_incident_record(db, "inc-1", state, "active")
    updated = store.upsert_incident_record(db, "inc-1", {**state, "retry_count": 2}, "resolved")

    assert created.incident_id == "inc-1"
    assert updated.retry_count == 2
    assert len(db.added) == 1
    assert refreshed


def test_append_audit_event_serializes_details():
    refreshed = []

    class FakeDB:
        def __init__(self):
            self.entries = []

        def add(self, entry):
            self.entries.append(entry)

        def commit(self):
            return None

        def refresh(self, entry):
            refreshed.append(entry)

    @dataclass
    class Detail:
        value: str

    db = FakeDB()
    entry = store.append_audit_event(db, "inc-1", "state_update", "system", {"detail": Detail("x")})

    assert entry.incident_id == "inc-1"
    assert db.entries[0].details == {"detail": {"value": "x"}}
    assert refreshed


def test_parse_dt_parses_iso_value():
    parsed = store._parse_dt("2026-03-30T10:00:00")

    assert isinstance(parsed, datetime)
    assert parsed.isoformat() == "2026-03-30T10:00:00"


def test_control_plane_summary_reflects_metric_updates():
    class FakeValue:
        def __init__(self):
            self.value = 0.0

        def get(self):
            return self.value

    class FakeCounter:
        def __init__(self):
            self._value = FakeValue()

        def inc(self):
            self._value.value += 1.0

    class FakeGauge:
        def __init__(self):
            self._value = FakeValue()

        def set(self, value):
            self._value.value = float(value)

    metrics.incidents_detected_total = FakeCounter()
    metrics.incidents_resolved_total = FakeCounter()
    metrics.hitl_interventions_total = FakeCounter()
    metrics.avg_time_to_recovery_seconds = FakeGauge()
    metrics._recovery_count = 0
    metrics._recovery_sum = 0.0

    metrics.observe_incident_detected()
    metrics.observe_hitl_intervention()
    metrics.observe_incident_resolved(30.0)

    summary = metrics.control_plane_summary()

    assert summary["incidents_detected_total"] == 1.0
    assert summary["hitl_interventions_total"] == 1.0
    assert summary["incidents_resolved_total"] == 1.0
    assert summary["avg_time_to_recovery_seconds"] == 30.0


def test_build_graph_falls_back_to_memory_when_postgres_unavailable(monkeypatch):
    monkeypatch.setattr(graph_module, "DATABASE_URL", "postgresql://invalid")

    class FailingPostgresSaver:
        @classmethod
        def from_conn_string(cls, conn):
            raise RuntimeError("db unavailable")

    monkeypatch.setitem(
        __import__("sys").modules,
        "langgraph.checkpoint.postgres",
        SimpleNamespace(PostgresSaver=FailingPostgresSaver),
    )

    compiled = graph_module.build_graph(use_postgres=True)

    assert compiled is not None
