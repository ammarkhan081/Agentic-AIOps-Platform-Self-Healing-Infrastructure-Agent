"""
Unit tests for HITL timeout handling and repeated-incident memory reuse.
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

from src.agents.learning import learning_agent
from src.agents.root_cause import root_cause_agent
from src.graph.state import AlertEvent, LogLine, PastIncident, TraceSpan, initial_state

incidents = importlib.import_module("src.api.routes.incidents")


def _make_alert() -> AlertEvent:
    return AlertEvent.create(
        service="order-service",
        metric_name="order_memory_leak_bytes",
        current=4096.0,
        mean=256.0,
        std=64.0,
        threshold=2.5,
        severity="CRITICAL",
    )


def test_hitl_timeout_watchdog_escalates_paused_incident(monkeypatch):
    incident_id = "inc-hitl-timeout"
    state = initial_state(incident_id=incident_id)
    state["status"] = "paused"
    state["hitl_required"] = True
    incidents._incidents[incident_id] = {"state": state, "status": "paused"}

    persisted = []
    events = []
    broadcasts = []

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(incidents.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        incidents,
        "_persist_snapshot",
        lambda iid, status, snapshot: persisted.append((iid, status, snapshot)),
    )
    monkeypatch.setattr(
        incidents,
        "_persist_event",
        lambda iid, action, actor, details: events.append((iid, action, actor, details)),
    )
    monkeypatch.setattr(incidents, "_broadcast", lambda iid, event: broadcasts.append((iid, event)))
    notified = []
    monkeypatch.setattr(
        incidents,
        "send_timeout_notification",
        lambda iid, state, timeout_seconds: notified.append((iid, timeout_seconds)) or True,
    )

    asyncio.run(incidents._hitl_timeout_watchdog(incident_id, "system", 1))

    assert incidents._incidents[incident_id]["status"] == "escalated"
    assert incidents._incidents[incident_id]["state"]["error_message"].startswith(
        "HITL timeout exceeded"
    )
    assert persisted[-1][1] == "escalated"
    assert events[-1][1] == "hitl_timeout"
    assert broadcasts[-1][1]["type"] == "hitl_timeout"
    assert notified[-1] == (incident_id, 1)

    incidents._incidents.pop(incident_id, None)


def test_repeated_incident_uses_memory_in_root_cause_prompt(monkeypatch):
    captured_postmortems = []

    monkeypatch.setattr(
        "src.agents.learning.upsert_incident",
        lambda postmortem: captured_postmortems.append(postmortem),
    )

    prior_state = initial_state("inc-prior")
    prior_state["alert"] = _make_alert()
    prior_state["recovery_confirmed"] = True
    prior_state["selected_fix"] = SimpleNamespace(
        action_type="restart_container", parameters={"service": "order-service"}
    )
    prior_state["created_at"] = "2026-03-30T10:00:00"
    prior_state["current_hypothesis_idx"] = 0
    prior_state["hypotheses"] = [SimpleNamespace(description="Memory leak in worker cache")]

    learned_state = learning_agent(prior_state)
    stored = captured_postmortems[0]
    assert learned_state["postmortem"].root_cause_confirmed == "Memory leak in worker cache"

    prompt_capture = {}

    class FakeLLM:
        def invoke(self, messages):
            prompt_capture["prompt"] = messages[1].content
            return SimpleNamespace(
                content='[{"hypothesis_id":"h1","description":"Memory leak in worker cache","confidence":0.91,"evidence":["similar past incident matched"],"suggested_fix_category":"restart"}]'
            )

    monkeypatch.setattr(
        "src.agents.root_cause.fetch_logs",
        lambda service, limit: [
            LogLine(
                timestamp="2026-03-30T10:10:00",
                level="ERROR",
                message="OOM near cache worker",
                service=service,
            )
        ],
    )
    monkeypatch.setattr(
        "src.agents.root_cause.fetch_traces",
        lambda service, limit: [
            TraceSpan(
                trace_id="t1",
                span_id="s1",
                operation_name="GET /orders",
                service=service,
                duration_ms=4200,
                status="error",
            )
        ],
    )
    monkeypatch.setattr(
        "src.agents.root_cause.search_similar_incidents",
        lambda query, service, top_k: [
            PastIncident(
                incident_id=stored.incident_id,
                service=stored.service,
                alert_signature=stored.alert_signature,
                root_cause=stored.root_cause_confirmed,
                fix_applied=stored.fix_applied,
                outcome=stored.outcome,
                time_to_recovery_seconds=stored.time_to_recovery_seconds or 0.0,
                similarity_score=0.98,
                occurred_at=stored.created_at,
            )
        ],
    )
    monkeypatch.setattr("src.agents.root_cause.get_chat_model", lambda **kwargs: FakeLLM())

    repeated_state = initial_state("inc-repeat")
    repeated_state["alert"] = _make_alert()

    result = root_cause_agent(repeated_state)

    assert len(result["past_incidents"]) == 1
    assert result["hypotheses"][0].description == "Memory leak in worker cache"
    assert "Past incident 1:" in prompt_capture["prompt"]
    assert stored.root_cause_confirmed in prompt_capture["prompt"]


def test_root_cause_uses_cached_response_when_context_matches(monkeypatch):
    alert = _make_alert()
    state = initial_state("inc-cache")
    state["alert"] = alert

    monkeypatch.setattr(
        "src.agents.root_cause.fetch_logs",
        lambda service, limit: [
            LogLine(
                timestamp="2026-03-30T10:10:00",
                level="ERROR",
                message="OOM near cache worker",
                service=service,
            )
        ],
    )
    monkeypatch.setattr(
        "src.agents.root_cause.fetch_traces",
        lambda service, limit: [
            TraceSpan(
                trace_id="t1",
                span_id="s1",
                operation_name="GET /orders",
                service=service,
                duration_ms=4200,
                status="error",
            )
        ],
    )
    monkeypatch.setattr(
        "src.agents.root_cause.search_similar_incidents", lambda query, service, top_k: []
    )

    invoke_count = {"count": 0}

    class FakeLLM:
        def invoke(self, messages):
            invoke_count["count"] += 1
            return SimpleNamespace(
                content='[{"hypothesis_id":"h1","description":"Memory leak in worker cache","confidence":0.91,"evidence":["cache pressure"],"suggested_fix_category":"restart"}]'
            )

    cache_store = {}

    class FakeRedis:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, key):
            return cache_store.get(key)

        def setex(self, key, ttl, value):
            cache_store[key] = value

    monkeypatch.setattr("src.agents.root_cause.get_chat_model", lambda **kwargs: FakeLLM())
    sys.modules["redis"] = SimpleNamespace(
        Redis=SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis())
    )

    first = root_cause_agent(state)
    second = root_cause_agent(state)

    assert first["hypotheses"][0].description == "Memory leak in worker cache"
    assert second["hypotheses"][0].description == "Memory leak in worker cache"
    assert invoke_count["count"] == 1


def test_root_cause_reuses_existing_hypotheses_without_fetch(monkeypatch):
    state = initial_state("inc-replan")
    state["alert"] = _make_alert()
    state["current_hypothesis_idx"] = 1
    state["hypotheses"] = [
        SimpleNamespace(description="first", confidence=0.5),
        SimpleNamespace(description="second", confidence=0.4),
    ]

    monkeypatch.setattr(
        "src.agents.root_cause.fetch_logs",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not fetch")),
    )

    result = root_cause_agent(state)

    assert result["hypotheses"][1].description == "second"
    assert result["current_hypothesis_idx"] == 1


def test_root_cause_returns_error_when_alert_missing():
    result = root_cause_agent(initial_state("inc-no-alert"))

    assert result["error_message"] == "No alert to analyze"


def test_root_cause_cache_read_failure_is_tolerated(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(
            Redis=SimpleNamespace(
                from_url=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("redis down"))
            )
        ),
    )

    assert importlib.import_module("src.agents.root_cause")._get_cached_hypotheses("key") is None


def test_root_cause_cache_write_failure_is_tolerated(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(
            Redis=SimpleNamespace(
                from_url=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("redis down"))
            )
        ),
    )

    importlib.import_module("src.agents.root_cause")._set_cached_hypotheses("key", [])


def test_root_cause_budget_trim_and_parse_failure(monkeypatch):
    root_cause = importlib.import_module("src.agents.root_cause")
    alert = _make_alert()
    state = initial_state("inc-parse-fail")
    state["alert"] = alert

    long_logs = [
        LogLine(
            timestamp=f"2026-03-30T10:{i:02d}:00",
            level="ERROR",
            message="x" * 400,
            service=alert.service,
        )
        for i in range(20)
    ]
    long_traces = [
        TraceSpan(
            trace_id=f"t{i}",
            span_id=f"s{i}",
            operation_name="GET /orders",
            service=alert.service,
            duration_ms=5000,
            status="error",
        )
        for i in range(10)
    ]
    long_past = [
        PastIncident(
            incident_id=f"p{i}",
            service=alert.service,
            alert_signature="sig",
            root_cause="memory leak",
            fix_applied="restart_container",
            outcome="resolved",
            time_to_recovery_seconds=10.0,
            similarity_score=0.9,
            occurred_at="2026-03-30T10:00:00",
        )
        for i in range(5)
    ]

    class FakeLLM:
        def invoke(self, messages):
            return SimpleNamespace(content="not-json")

    monkeypatch.setattr("src.agents.root_cause.fetch_logs", lambda service, limit: long_logs)
    monkeypatch.setattr("src.agents.root_cause.fetch_traces", lambda service, limit: long_traces)
    monkeypatch.setattr(
        "src.agents.root_cause.search_similar_incidents", lambda query, service, top_k: long_past
    )
    monkeypatch.setattr("src.agents.root_cause.get_chat_model", lambda **kwargs: FakeLLM())
    monkeypatch.setattr("src.agents.root_cause.TOKEN_BUDGET", 200)

    prompt = root_cause._build_user_prompt_with_budget(
        alert, long_logs, long_traces, long_past, 200
    )
    result = root_cause_agent(state)

    assert root_cause._approx_tokens(prompt) <= 200
    assert result["hitl_required"] is True
    assert result["hypotheses"] == []


def test_root_cause_normalizes_unknown_fix_category(monkeypatch):
    alert = _make_alert()
    state = initial_state("inc-unknown-category")
    state["alert"] = alert

    class FakeLLM:
        def invoke(self, messages):
            return SimpleNamespace(
                content='[{"hypothesis_id":"h1","description":"weird issue","confidence":0.5,"evidence":["x"],"suggested_fix_category":"strange"}]'
            )

    monkeypatch.setattr("src.agents.root_cause.fetch_logs", lambda service, limit: [])
    monkeypatch.setattr("src.agents.root_cause.fetch_traces", lambda service, limit: [])
    monkeypatch.setattr(
        "src.agents.root_cause.search_similar_incidents", lambda query, service, top_k: []
    )
    monkeypatch.setattr("src.agents.root_cause.get_chat_model", lambda **kwargs: FakeLLM())

    result = root_cause_agent(state)

    assert result["hypotheses"][0].suggested_fix_category == "unknown"
