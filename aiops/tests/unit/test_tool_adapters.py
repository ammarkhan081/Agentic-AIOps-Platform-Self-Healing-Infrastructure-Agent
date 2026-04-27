"""
Unit tests for observability and integration tool adapters.
"""

from __future__ import annotations

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

from src.graph.state import Postmortem

prometheus_tool = importlib.import_module("src.tools.prometheus_tool")
loki_tool = importlib.import_module("src.tools.loki_tool")
jaeger_tool = importlib.import_module("src.tools.jaeger_tool")
slack_tool = importlib.import_module("src.tools.slack_tool")
pinecone_tool = importlib.import_module("src.tools.pinecone_tool")


def test_prometheus_query_instant_success(monkeypatch):
    monkeypatch.setattr(
        prometheus_tool.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {"status": "success", "data": {"result": [{"value": [0, "3.5"]}]}}
        ),
    )

    assert prometheus_tool.query_instant("up") == 3.5


def test_prometheus_query_range_filters_nan(monkeypatch):
    monkeypatch.setattr(
        prometheus_tool.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {
                "status": "success",
                "data": {"result": [{"values": [[0, "1.0"], [1, "NaN"], [2, "2.5"]]}]},
            }
        ),
    )

    assert prometheus_tool.query_range("rate(x[5m])") == [1.0, 2.5]


def test_prometheus_health_check_true(monkeypatch):
    monkeypatch.setattr(
        prometheus_tool.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200),
    )

    assert prometheus_tool.health_check() is True


def test_prometheus_query_instant_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(
        prometheus_tool.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert prometheus_tool.query_instant("up") is None


def test_loki_fetch_logs_parses_json(monkeypatch):
    payload = {
        "data": {
            "result": [
                {
                    "values": [
                        [
                            "1",
                            '{"time":"2026-03-30T10:00:00","level":"ERROR","message":"bad","service":"order-service"}',
                        ]
                    ]
                }
            ]
        }
    }
    monkeypatch.setattr(
        loki_tool.httpx, "get", lambda *args, **kwargs: SimpleNamespace(json=lambda: payload)
    )

    logs = loki_tool.fetch_logs("order-service", limit=5)

    assert logs[0].level == "ERROR"
    assert logs[0].message == "bad"


def test_loki_fetch_logs_falls_back_to_plain_text(monkeypatch):
    payload = {"data": {"result": [{"values": [["1", "plain log line"]]}]}}
    monkeypatch.setattr(
        loki_tool.httpx, "get", lambda *args, **kwargs: SimpleNamespace(json=lambda: payload)
    )

    logs = loki_tool.fetch_logs("order-service", limit=5)

    assert logs[0].level == "INFO"
    assert logs[0].message == "plain log line"


def test_loki_health_check_false_on_error(monkeypatch):
    monkeypatch.setattr(
        loki_tool.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")),
    )

    assert loki_tool.health_check() is False


def test_jaeger_fetch_traces_builds_spans(monkeypatch):
    payload = {
        "data": [
            {
                "spans": [
                    {
                        "traceID": "t1",
                        "spanID": "s1",
                        "operationName": "GET /orders",
                        "duration": 5000,
                        "tags": [{"key": "error", "value": True}],
                    }
                ]
            }
        ]
    }
    monkeypatch.setattr(
        jaeger_tool.httpx, "get", lambda *args, **kwargs: SimpleNamespace(json=lambda: payload)
    )

    spans = jaeger_tool.fetch_traces("order-service", limit=5)

    assert spans[0].status == "error"
    assert spans[0].duration_ms == 5.0


def test_jaeger_health_check_true(monkeypatch):
    monkeypatch.setattr(
        jaeger_tool.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200),
    )

    assert jaeger_tool.health_check() is True


def test_slack_builders_include_core_fields():
    state = {
        "incident_id": "inc-1",
        "retry_count": 1,
        "alert": SimpleNamespace(service="order-service", severity="CRITICAL"),
        "hypotheses": [SimpleNamespace(description="memory leak")],
        "selected_fix": SimpleNamespace(action_type="restart_container", risk_score="LOW"),
        "current_hypothesis_idx": 0,
    }

    hitl = slack_tool.build_hitl_message(state)
    timeout = slack_tool.build_timeout_message("inc-1", state, 900)

    assert "ASHIA human approval required" in hitl["text"]
    assert "Timeout: 900s" in timeout["text"]


def test_send_hitl_notification_returns_false_without_webhook(monkeypatch):
    monkeypatch.setattr(slack_tool, "SLACK_WEBHOOK_URL", "")

    assert slack_tool.send_hitl_notification({}) is False


def test_send_hitl_notification_success(monkeypatch):
    monkeypatch.setattr(slack_tool, "SLACK_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setattr(
        slack_tool.httpx,
        "post",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, text="ok"),
    )

    assert slack_tool.send_hitl_notification({"incident_id": "inc-1"}) is True


def test_send_hitl_notification_non_200(monkeypatch):
    monkeypatch.setattr(slack_tool, "SLACK_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setattr(
        slack_tool.httpx,
        "post",
        lambda *args, **kwargs: SimpleNamespace(status_code=500, text="bad"),
    )

    assert slack_tool.send_hitl_notification({"incident_id": "inc-1"}) is False


def test_send_timeout_notification_success(monkeypatch):
    monkeypatch.setattr(slack_tool, "SLACK_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setattr(
        slack_tool.httpx,
        "post",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, text="ok"),
    )

    assert slack_tool.send_timeout_notification("inc-1", {"status": "escalated"}, 600) is True


def test_send_timeout_notification_returns_false_without_webhook(monkeypatch):
    monkeypatch.setattr(slack_tool, "SLACK_WEBHOOK_URL", "")

    assert slack_tool.send_timeout_notification("inc-1", {"status": "escalated"}, 600) is False


def test_send_timeout_notification_handles_exception(monkeypatch):
    monkeypatch.setattr(slack_tool, "SLACK_WEBHOOK_URL", "https://example.test/webhook")
    monkeypatch.setattr(
        slack_tool.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert slack_tool.send_timeout_notification("inc-1", {"status": "escalated"}, 600) is False


def test_pinecone_build_embedding_text_contains_fields():
    text = pinecone_tool._build_embedding_text(
        Postmortem(
            incident_id="inc-1",
            service="order-service",
            alert_signature="sig",
            root_cause_confirmed="memory leak",
            fix_applied="restart_container",
            outcome="resolved",
            time_to_recovery_seconds=45.0,
            retry_count=1,
            total_cost_usd=0.01,
            created_at="2026-03-30T10:00:00",
        )
    )

    assert "service=order-service" in text
    assert "root_cause=memory leak" in text


def test_pinecone_memory_status_uses_settings(monkeypatch):
    monkeypatch.setattr(
        pinecone_tool,
        "get_settings",
        lambda: SimpleNamespace(
            pinecone_index_name="ashia-incidents", pinecone_namespace="production"
        ),
    )

    status = pinecone_tool.memory_status()

    assert status["provider"] == "pinecone"
    assert status["index"] == "ashia-incidents"


def test_pinecone_search_returns_empty_when_unavailable(monkeypatch):
    monkeypatch.setattr(
        pinecone_tool, "_get_clients", lambda: (_ for _ in ()).throw(RuntimeError("offline"))
    )

    assert pinecone_tool.search_similar_incidents("memory leak", "order-service") == []


def test_pinecone_delete_incident_returns_false_on_failure(monkeypatch):
    monkeypatch.setattr(
        pinecone_tool, "_get_clients", lambda: (_ for _ in ()).throw(RuntimeError("offline"))
    )

    assert pinecone_tool.delete_incident("inc-1") is False


def test_pinecone_export_memory_snapshot_is_json(monkeypatch):
    monkeypatch.setattr(
        pinecone_tool,
        "memory_status",
        lambda: {"provider": "pinecone", "index": "ashia-incidents", "namespace": "production"},
    )

    snapshot = pinecone_tool.export_memory_snapshot()

    assert '"provider": "pinecone"' in snapshot
