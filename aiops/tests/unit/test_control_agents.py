"""
Unit tests for verifier and HITL agent control paths.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.agents.hitl import _override_to_fix, hitl_supervisor
from src.agents.verifier import _metric_recovered, verifier_agent
from src.graph.state import AlertEvent, HumanDecision, initial_state


def make_alert(metric_name: str = "order_memory_leak_bytes") -> AlertEvent:
    return AlertEvent.create(
        service="order-service",
        metric_name=metric_name,
        current=4096.0,
        mean=256.0,
        std=64.0,
        threshold=2.5,
        severity="CRITICAL",
    )


def test_override_to_fix_maps_restart_instruction():
    state = initial_state("inc-hitl-1")
    state["alert"] = make_alert()

    fix = _override_to_fix("Please restart the service now", state)

    assert fix.action_type == "restart_container"
    assert fix.parameters["container"] == "order-service"
    assert fix.risk_score == "MEDIUM"


def test_hitl_supervisor_pauses_and_notifies(monkeypatch):
    state = initial_state("inc-hitl-2")
    state["selected_fix"] = SimpleNamespace(action_type="scale_up")
    notified = []
    monkeypatch.setattr(
        "src.agents.hitl.send_hitl_notification",
        lambda current_state: notified.append(current_state["incident_id"]) or True,
    )

    result = hitl_supervisor(state)

    assert result["status"] == "paused"
    assert result["hitl_required"] is True
    assert notified == ["inc-hitl-2"]


def test_hitl_supervisor_abort_escalates():
    state = initial_state("inc-hitl-3")
    state["hitl_response"] = HumanDecision(
        decision="abort",
        custom_instruction=None,
        decided_by="admin",
        decided_at="2026-03-30T10:00:00",
        reason="unsafe",
    )

    result = hitl_supervisor(state)

    assert result["status"] == "escalated"
    assert result["hitl_required"] is False


def test_hitl_supervisor_override_executes_fix(monkeypatch):
    state = initial_state("inc-hitl-4")
    state["alert"] = make_alert()
    state["hitl_response"] = HumanDecision(
        decision="override",
        custom_instruction="restart the service",
        decided_by="admin",
        decided_at="2026-03-30T10:05:00",
        reason="approved",
    )
    monkeypatch.setattr(
        "src.agents.hitl._execute_fix",
        lambda fix: SimpleNamespace(
            action_type=fix.action_type, parameters=fix.parameters, outcome="success"
        ),
    )

    result = hitl_supervisor(state)

    assert result["selected_fix"].action_type == "restart_container"
    assert result["execution_log"][-1].outcome == "success"
    assert result["hitl_required"] is False


def test_metric_recovered_returns_true_for_unknown_metric():
    assert _metric_recovered(make_alert("unknown_metric"), {}) is True


def test_metric_recovered_compares_against_threshold(monkeypatch):
    monkeypatch.setattr("src.agents.verifier._query_metric", lambda query: 280.0)

    recovered = _metric_recovered(make_alert("order_memory_leak_bytes"), {})

    assert recovered is True


def test_verifier_agent_confirms_recovery(monkeypatch):
    state = initial_state("inc-verify-1")
    state["alert"] = make_alert("order_memory_leak_bytes")
    state["selected_fix"] = SimpleNamespace(action_type="restart_container")
    monkeypatch.setattr("src.agents.verifier.time.sleep", lambda seconds: None)
    monkeypatch.setattr("src.agents.verifier._metric_recovered", lambda alert, before: True)
    monkeypatch.setattr(
        "src.agents.verifier._post_fix_metric_snapshot", lambda: {"order_memory_leak_bytes": 128.0}
    )

    result = verifier_agent(state)

    assert result["recovery_confirmed"] is True
    assert result["recovery_metrics"]["order_memory_leak_bytes"] == 128.0


def test_verifier_agent_advances_retry_and_hypothesis(monkeypatch):
    state = initial_state("inc-verify-2")
    state["alert"] = make_alert("order_memory_leak_bytes")
    state["selected_fix"] = SimpleNamespace(action_type="restart_container")
    state["current_hypothesis_idx"] = 1
    monkeypatch.setattr("src.agents.verifier.time.sleep", lambda seconds: None)
    monkeypatch.setattr("src.agents.verifier._metric_recovered", lambda alert, before: False)
    monkeypatch.setattr(
        "src.agents.verifier._post_fix_metric_snapshot", lambda: {"order_memory_leak_bytes": 1024.0}
    )

    result = verifier_agent(state)

    assert result["recovery_confirmed"] is False
    assert result["retry_count"] == 1
    assert result["current_hypothesis_idx"] == 2
