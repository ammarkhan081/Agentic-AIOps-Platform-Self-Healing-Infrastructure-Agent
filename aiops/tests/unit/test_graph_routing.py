"""
Unit tests — Graph State and Conditional Edge Functions
Tests: initial_state, all 4 routing functions
"""

from src.graph.edges import (
    MAX_RETRIES,
    route_after_hitl,
    route_after_monitor,
    route_after_remediation,
    route_after_verifier,
)
from src.graph.state import AlertEvent, FixOption, Hypothesis, initial_state


def _make_alert():
    return AlertEvent.create(
        service="order-service",
        metric_name="order_memory_leak_bytes",
        current=5000.0,
        mean=100.0,
        std=50.0,
        threshold=2.5,
        severity="CRITICAL",
    )


def _make_hypothesis(confidence=0.85, attempted=False):
    return Hypothesis(
        hypothesis_id="h1",
        description="Memory leak in order service",
        confidence=confidence,
        evidence=["log shows OOM"],
        suggested_fix_category="restart",
        attempted=attempted,
    )


def _make_fix(risk="LOW"):
    return FixOption(
        fix_id="f1",
        action_type="restart_container",
        parameters={"container": "order-service"},
        risk_score=risk,
        estimated_recovery_seconds=30,
        reasoning="Restart will clear memory",
    )


class TestInitialState:
    def test_default_values(self):
        s = initial_state()
        assert s["status"] == "active"
        assert s["retry_count"] == 0
        assert s["hitl_required"] is False
        assert s["alert"] is None
        assert s["hypotheses"] == []
        assert s["execution_log"] == []
        assert s["total_cost_usd"] == 0.0

    def test_custom_incident_id(self):
        s = initial_state(incident_id="test-123")
        assert s["incident_id"] == "test-123"

    def test_unique_incident_ids(self):
        s1 = initial_state()
        s2 = initial_state()
        assert s1["incident_id"] != s2["incident_id"]


class TestRouteAfterMonitor:
    def test_routes_to_root_cause_when_alert(self):
        s = initial_state()
        s["alert"] = _make_alert()
        assert route_after_monitor(s) == "root_cause"

    def test_routes_to_end_when_no_alert(self):
        s = initial_state()
        assert route_after_monitor(s) == "__end__"


class TestRouteAfterRemediation:
    def test_routes_to_hitl_when_required(self):
        s = initial_state()
        s["hitl_required"] = True
        assert route_after_remediation(s) == "hitl"

    def test_routes_to_verifier_when_low_risk(self):
        s = initial_state()
        s["hitl_required"] = False
        assert route_after_remediation(s) == "verifier"


class TestRouteAfterHITL:
    def test_routes_to_learning_when_aborted(self):
        s = initial_state()
        from datetime import datetime

        from src.graph.state import HumanDecision

        s["hitl_response"] = HumanDecision(
            decision="abort",
            custom_instruction=None,
            decided_by="admin",
            decided_at=datetime.utcnow().isoformat(),
        )
        assert route_after_hitl(s) == "learning"

    def test_routes_to_verifier_when_approved(self):
        s = initial_state()
        from datetime import datetime

        from src.graph.state import HumanDecision

        s["hitl_response"] = HumanDecision(
            decision="approve",
            custom_instruction=None,
            decided_by="admin",
            decided_at=datetime.utcnow().isoformat(),
        )
        s["status"] = "active"
        assert route_after_hitl(s) == "verifier"

    def test_routes_to_end_when_paused_without_decision(self):
        s = initial_state()
        s["status"] = "paused"
        s["hitl_response"] = None
        assert route_after_hitl(s) == "__end__"


class TestRouteAfterVerifier:
    def test_routes_to_learning_when_recovered(self):
        s = initial_state()
        s["recovery_confirmed"] = True
        assert route_after_verifier(s) == "learning"

    def test_routes_to_hitl_when_max_retries_exceeded(self):
        s = initial_state()
        s["recovery_confirmed"] = False
        s["retry_count"] = MAX_RETRIES
        assert route_after_verifier(s) == "hitl"

    def test_routes_to_root_cause_when_retries_left(self):
        s = initial_state()
        s["recovery_confirmed"] = False
        s["retry_count"] = 0
        s["hypotheses"] = [_make_hypothesis(), _make_hypothesis(confidence=0.6)]
        s["current_hypothesis_idx"] = 0
        assert route_after_verifier(s) == "root_cause"

    def test_routes_to_hitl_when_no_more_hypotheses(self):
        s = initial_state()
        s["recovery_confirmed"] = False
        s["retry_count"] = 0
        s["hypotheses"] = [_make_hypothesis()]
        s["current_hypothesis_idx"] = (
            1  # verifier has already advanced index past available hypotheses
        )
        assert route_after_verifier(s) == "hitl"
