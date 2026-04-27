"""
Unit tests for remaining agent edge cases across learning, HITL, verifier, and monitor.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from src.graph.state import AlertEvent, HumanDecision, Hypothesis, initial_state

learning = importlib.import_module("src.agents.learning")
hitl = importlib.import_module("src.agents.hitl")
verifier = importlib.import_module("src.agents.verifier")
monitor = importlib.import_module("src.agents.monitor")


def _make_alert(
    metric_name: str = "order_memory_leak_bytes", service: str = "order-service"
) -> AlertEvent:
    return AlertEvent.create(
        service=service,
        metric_name=metric_name,
        current=100.0,
        mean=10.0,
        std=5.0,
        threshold=2.5,
        severity="HIGH",
    )


def test_learning_agent_resolved_path(monkeypatch):
    captured = []
    monkeypatch.setattr(learning, "upsert_incident", lambda postmortem: captured.append(postmortem))

    state = initial_state("inc-learn-1")
    state["alert"] = _make_alert()
    state["recovery_confirmed"] = True
    state["selected_fix"] = SimpleNamespace(
        action_type="restart_container", parameters={"container": "order-service"}
    )
    state["hypotheses"] = [Hypothesis("h1", "memory leak", 0.9, ["memory"], "restart")]
    state["created_at"] = "2026-03-30T10:00:00"
    state["total_cost_usd"] = 0.12345

    result = learning.learning_agent(state)

    assert result["status"] == "resolved"
    assert result["postmortem"].outcome == "resolved"
    assert "restart_container" in result["postmortem"].fix_applied
    assert captured[0].incident_id == "inc-learn-1"


def test_learning_agent_escalated_and_invalid_created_at(monkeypatch):
    captured = []
    monkeypatch.setattr(learning, "upsert_incident", lambda postmortem: captured.append(postmortem))

    state = initial_state("inc-learn-2")
    state["alert"] = _make_alert("gateway_error_rate", "api-gateway")
    state["hitl_required"] = True
    state["created_at"] = "not-a-date"

    result = learning.learning_agent(state)

    assert result["status"] == "escalated"
    assert result["postmortem"].time_to_recovery_seconds is None
    assert captured[0].service == "api-gateway"


def test_learning_agent_handles_memory_write_failure(monkeypatch):
    monkeypatch.setattr(
        learning,
        "upsert_incident",
        lambda postmortem: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    state = initial_state("inc-learn-3")
    state["alert"] = _make_alert()

    result = learning.learning_agent(state)

    assert result["postmortem"].incident_id == "inc-learn-3"
    assert result["status"] == "escalated"


def test_hitl_override_to_fix_maps_cache_db_rollback_and_fallback():
    state = initial_state("inc-hitl-edge")
    state["alert"] = _make_alert(service="api-gateway")

    cache_fix = hitl._override_to_fix("flush cache now", state)
    db_fix = hitl._override_to_fix("reset db pool", state)
    rollback_fix = hitl._override_to_fix("rollback immediately", state)
    fallback_fix = hitl._override_to_fix("inspect manually", state)

    assert cache_fix.action_type == "flush_cache"
    assert db_fix.action_type == "db_connection_reset"
    assert rollback_fix.action_type == "image_rollback"
    assert fallback_fix.action_type == "manual_investigation"


def test_hitl_supervisor_approve_without_selected_fix_adds_skipped_log():
    state = initial_state("inc-hitl-no-fix")
    state["hitl_response"] = HumanDecision(
        decision="approve",
        custom_instruction=None,
        decided_by="admin",
        decided_at="2026-03-30T10:00:00",
    )

    result = hitl.hitl_supervisor(state)

    assert result["status"] == "active"
    assert result["execution_log"][-1].outcome == "skipped"


def test_verifier_query_metric_handles_failure(monkeypatch):
    monkeypatch.setattr(
        verifier.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")),
    )

    assert verifier._query_metric("up") is None


def test_verifier_metric_recovered_false_when_current_missing(monkeypatch):
    monkeypatch.setattr(verifier, "_query_metric", lambda query: None)

    assert verifier._metric_recovered(_make_alert(), {}) is False


def test_verifier_post_fix_metric_snapshot_collects_values(monkeypatch):
    monkeypatch.setattr(verifier, "_query_metric", lambda query: 1.5 if "order" in query else None)

    snapshot = verifier._post_fix_metric_snapshot()

    assert "order_error_rate" in snapshot
    assert "user_error_rate" not in snapshot


def test_verifier_agent_handles_missing_alert():
    result = verifier.verifier_agent({"retry_count": 0, "selected_fix": None})

    assert result["recovery_confirmed"] is False


def test_verifier_agent_max_retry_path(monkeypatch):
    state = initial_state("inc-verify-max")
    state["alert"] = _make_alert()
    state["retry_count"] = verifier.MAX_RETRIES - 1
    state["selected_fix"] = SimpleNamespace(action_type="restart_container")

    monkeypatch.setattr(verifier.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(verifier, "_metric_recovered", lambda alert, before: False)
    monkeypatch.setattr(
        verifier, "_post_fix_metric_snapshot", lambda: {"order_memory_leak_bytes": 100.0}
    )

    result = verifier.verifier_agent(state)

    assert result["retry_count"] == verifier.MAX_RETRIES
    assert result["recovery_confirmed"] is False


def test_monitor_query_prometheus_success_and_retry(monkeypatch):
    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        return SimpleNamespace(
            json=lambda: {"status": "success", "data": {"result": [{"value": [0, "3.2"]}]}}
        )

    monkeypatch.setattr(monitor.httpx, "get", fake_get)
    monkeypatch.setattr(monitor.time, "sleep", lambda seconds: None)

    assert monitor._query_prometheus("up") == 3.2


def test_monitor_query_range_success(monkeypatch):
    monkeypatch.setattr(
        monitor.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {
                "status": "success",
                "data": {"result": [{"values": [[0, "1.0"], [1, "NaN"], [2, "2.0"]]}]},
            }
        ),
    )

    assert monitor._query_range("up") == [1.0, 2.0]


def test_monitor_meta_alert_and_skip_existing_alert(monkeypatch):
    for key in monitor._consecutive_counts:
        monitor._consecutive_counts[key] = 0
    monitor._prometheus_consecutive_fail_runs = monitor.PROMETHEUS_FAILURE_ALERT_RUNS - 1

    monkeypatch.setattr(
        monitor,
        "_snapshot_all_metrics_with_health",
        lambda: ({name: 0.0 for name in monitor.METRIC_QUERIES}, len(monitor.METRIC_QUERIES)),
    )

    state = initial_state("inc-monitor-meta")
    result = monitor.monitor_agent(state)

    assert result["alert"].metric_name == "prometheus_unavailable"

    existing = initial_state("inc-monitor-existing")
    existing["alert"] = _make_alert()
    assert monitor.monitor_agent(existing) is existing


def test_monitor_snapshot_and_zero_std_paths(monkeypatch):
    monkeypatch.setattr(monitor, "_query_prometheus", lambda query: 1.0)
    snapshot = monitor._snapshot_all_metrics()
    assert len(snapshot) == len(monitor.METRIC_QUERIES)

    for key in monitor._consecutive_counts:
        monitor._consecutive_counts[key] = 2
    monitor._prometheus_consecutive_fail_runs = 0
    monkeypatch.setattr(
        monitor,
        "_snapshot_all_metrics_with_health",
        lambda: ({name: 1.0 for name in monitor.METRIC_QUERIES}, 0),
    )
    monkeypatch.setattr(monitor, "_query_range", lambda query, hours=24: [1.0] * 20)

    result = monitor.monitor_agent(initial_state("inc-monitor-std"))

    assert result.get("alert") is None
    assert all(value == 0 for value in monitor._consecutive_counts.values())
