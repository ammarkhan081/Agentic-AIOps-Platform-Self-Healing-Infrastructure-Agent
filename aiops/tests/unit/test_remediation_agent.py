"""
Unit tests for remediation risk and executor behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.agents import remediation
from src.graph.state import FixOption, Hypothesis, initial_state


def test_compute_risk_increases_for_critical_service(monkeypatch):
    class FakeDatetime:
        @staticmethod
        def utcnow():
            return SimpleNamespace(hour=10)

    monkeypatch.setattr(remediation, "datetime", FakeDatetime)

    risk = remediation._compute_risk("config_patch", "api-gateway", retry_count=1)

    assert risk == "HIGH"


def test_execute_fix_restart_container_success(monkeypatch):
    restarted = []

    class FakeContainer:
        def restart(self, timeout=10):
            restarted.append(timeout)

    class FakeClient:
        class containers:
            @staticmethod
            def get(name):
                assert name == "ashia-order-service"
                return FakeContainer()

    monkeypatch.setattr(remediation.docker, "from_env", lambda: FakeClient())

    result = remediation._execute_fix(
        FixOption(
            fix_id="f1",
            action_type="restart_container",
            parameters={"container": "order-service"},
            risk_score="LOW",
            estimated_recovery_seconds=30,
            reasoning="restart it",
        )
    )

    assert result.outcome == "success"
    assert restarted == [10]


def test_execute_fix_flush_cache_success(monkeypatch):
    flushed = []
    monkeypatch.setattr(remediation.docker, "from_env", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "redis",
        SimpleNamespace(
            Redis=SimpleNamespace(
                from_url=lambda url: SimpleNamespace(flushdb=lambda: flushed.append(url))
            )
        ),
    )

    result = remediation._execute_fix(
        FixOption(
            fix_id="f2",
            action_type="flush_cache",
            parameters={},
            risk_score="LOW",
            estimated_recovery_seconds=20,
            reasoning="flush cache",
        )
    )

    assert result.outcome == "success"
    assert flushed


def test_execute_fix_scale_up_calls_http(monkeypatch):
    calls = []
    monkeypatch.setattr(remediation.docker, "from_env", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "httpx",
        SimpleNamespace(
            post=lambda url, params=None, timeout=None: (
                calls.append((url, params)) or SimpleNamespace(text="ok")
            )
        ),
    )

    result = remediation._execute_fix(
        FixOption(
            fix_id="f3",
            action_type="scale_up",
            parameters={"service": "user-service", "replicas": 3},
            risk_score="MEDIUM",
            estimated_recovery_seconds=60,
            reasoning="scale service",
        )
    )

    assert result.outcome == "success"
    assert calls[0][1]["replicas"] == 3


def test_remediation_agent_auto_executes_low_risk(monkeypatch):
    state = initial_state("inc-remed-1")
    state["alert"] = SimpleNamespace(service="order-service")
    state["hypotheses"] = [
        Hypothesis(
            hypothesis_id="h1",
            description="memory leak",
            confidence=0.9,
            evidence=["memory"],
            suggested_fix_category="restart",
        )
    ]
    low_fix = FixOption(
        fix_id="f1",
        action_type="restart_container",
        parameters={"container": "order-service"},
        risk_score="LOW",
        estimated_recovery_seconds=30,
        reasoning="restart",
    )
    monkeypatch.setattr(
        remediation, "_generate_fix_options", lambda hypothesis, service, retry_count: [low_fix]
    )
    monkeypatch.setattr(
        remediation,
        "_execute_fix",
        lambda fix: SimpleNamespace(outcome="success", action_type=fix.action_type),
    )

    result = remediation.remediation_agent(state)

    assert result["hitl_required"] is False
    assert result["execution_log"][-1].outcome == "success"


def test_remediation_agent_pauses_high_risk(monkeypatch):
    state = initial_state("inc-remed-2")
    state["alert"] = SimpleNamespace(service="order-service")
    state["hypotheses"] = [
        Hypothesis(
            hypothesis_id="h1",
            description="bad deploy",
            confidence=0.9,
            evidence=["errors"],
            suggested_fix_category="rollback",
        )
    ]
    high_fix = FixOption(
        fix_id="f2",
        action_type="image_rollback",
        parameters={"service": "order-service"},
        risk_score="HIGH",
        estimated_recovery_seconds=120,
        reasoning="rollback",
    )
    monkeypatch.setattr(
        remediation, "_generate_fix_options", lambda hypothesis, service, retry_count: [high_fix]
    )

    result = remediation.remediation_agent(state)

    assert result["hitl_required"] is True
    assert result["selected_fix"].action_type == "image_rollback"


def test_execute_fix_memory_limit_update(monkeypatch):
    calls = []
    monkeypatch.setattr(remediation.docker, "from_env", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "httpx",
        SimpleNamespace(
            post=lambda url, timeout=5.0: calls.append((url, timeout)) or SimpleNamespace(text="ok")
        ),
    )

    result = remediation._execute_fix(
        FixOption(
            fix_id="f4",
            action_type="memory_limit_update",
            parameters={"container": "user-service"},
            risk_score="MEDIUM",
            estimated_recovery_seconds=30,
            reasoning="reset memory pressure",
        )
    )

    assert result.outcome == "success"
    assert calls[0][0].endswith("/fault/reset")


def test_execute_fix_config_patch_for_user_service(monkeypatch):
    calls = []
    monkeypatch.setattr(remediation.docker, "from_env", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "httpx",
        SimpleNamespace(
            post=lambda url, params=None, timeout=5.0: (
                calls.append((url, params)) or SimpleNamespace(text="patched")
            )
        ),
    )

    result = remediation._execute_fix(
        FixOption(
            fix_id="f5",
            action_type="config_patch",
            parameters={"service": "user-service", "max_connections": 77},
            risk_score="MEDIUM",
            estimated_recovery_seconds=30,
            reasoning="raise pool",
        )
    )

    assert result.outcome == "success"
    assert calls[0][1]["max_connections"] == 77


def test_execute_fix_db_connection_reset(monkeypatch):
    calls = []
    monkeypatch.setattr(remediation.docker, "from_env", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "httpx",
        SimpleNamespace(
            post=lambda url, timeout=5.0: calls.append(url) or SimpleNamespace(text="reset")
        ),
    )

    result = remediation._execute_fix(
        FixOption(
            fix_id="f6",
            action_type="db_connection_reset",
            parameters={},
            risk_score="HIGH",
            estimated_recovery_seconds=30,
            reasoning="clear stuck pool",
        )
    )

    assert result.outcome == "success"
    assert calls[0].endswith("/fault/reset")


def test_execute_fix_image_rollback(monkeypatch):
    calls = []
    monkeypatch.setattr(remediation.docker, "from_env", lambda: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "httpx",
        SimpleNamespace(
            post=lambda url, params=None, timeout=5.0: (
                calls.append((url, params)) or SimpleNamespace(text="rolled")
            )
        ),
    )

    result = remediation._execute_fix(
        FixOption(
            fix_id="f7",
            action_type="image_rollback",
            parameters={"service": "order-service", "target_version": "v0.8.0"},
            risk_score="HIGH",
            estimated_recovery_seconds=60,
            reasoning="rollback bad deploy",
        )
    )

    assert result.outcome == "success"
    assert calls[0][0].endswith("/fault/rollback")
    assert calls[0][1]["target_version"] == "v0.8.0"


def test_execute_fix_unknown_action_is_acknowledged():
    remediation.docker.from_env = lambda: object()
    result = remediation._execute_fix(
        FixOption(
            fix_id="f8",
            action_type="manual_investigation",
            parameters={},
            risk_score="HIGH",
            estimated_recovery_seconds=300,
            reasoning="needs human",
        )
    )

    assert result.outcome == "success"
    assert "no automated executor" in result.response


def test_generate_fix_options_falls_back_on_llm_failure(monkeypatch):
    class BrokenLLM:
        def invoke(self, messages):
            raise RuntimeError("llm unavailable")

    monkeypatch.setattr(remediation, "get_chat_model", lambda **kwargs: BrokenLLM())

    options = remediation._generate_fix_options(
        Hypothesis(
            hypothesis_id="h2",
            description="memory leak",
            confidence=0.9,
            evidence=["memory"],
            suggested_fix_category="restart",
        ),
        "order-service",
        0,
    )

    assert options[0].action_type == "manual_investigation"


def test_remediation_agent_handles_missing_hypotheses():
    result = remediation.remediation_agent(
        {"hypotheses": [], "current_hypothesis_idx": 0, "alert": None, "retry_count": 0}
    )

    assert result["hitl_required"] is True
    assert result["error_message"] == "No hypothesis available"
