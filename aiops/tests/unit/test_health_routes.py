"""
Unit tests for health/control-plane route helpers and endpoints.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

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

health = importlib.import_module("src.api.routes.health")


def test_metrics_control_plane_summary_route(monkeypatch):
    monkeypatch.setattr(
        health,
        "control_plane_summary",
        lambda: {
            "incidents_detected_total": 3.0,
            "incidents_resolved_total": 2.0,
            "hitl_interventions_total": 1.0,
            "avg_time_to_recovery_seconds": 42.5,
        },
    )

    result = asyncio.run(health.metrics_control_plane_summary(user={"role": "viewer"}))

    assert result["metrics"]["incidents_detected_total"] == 3.0
    assert result["metrics"]["avg_time_to_recovery_seconds"] == 42.5


def test_demo_fault_inject_cpu_spike_queues_background_task(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(order_service_url="http://order-service:8002"),
    )
    background_tasks = BackgroundTasks()

    result = asyncio.run(
        health.demo_fault_inject(
            health.DemoFaultRequest(fault_type="cpu_spike", duration=5),
            background_tasks,
            user={"role": "sre"},
        )
    )

    assert result["queued"] is True
    assert result["fault_type"] == "cpu_spike"
    assert len(background_tasks.tasks) == 1


def test_demo_fault_inject_executes_non_cpu_fault(monkeypatch):
    async def fake_execute(settings, body):
        return {
            "fault_type": body.fault_type,
            "service": "order-service",
            "result": {"status": "injected"},
        }

    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(order_service_url="http://order-service:8002"),
    )
    monkeypatch.setattr(health, "_execute_demo_fault", fake_execute)

    result = asyncio.run(
        health.demo_fault_inject(
            health.DemoFaultRequest(fault_type="memory_leak", cycles=3),
            BackgroundTasks(),
            user={"role": "admin"},
        )
    )

    assert result["queued"] is False
    assert result["fault_type"] == "memory_leak"
    assert result["result"]["status"] == "injected"


def test_execute_demo_fault_rollback_targets_order_service(monkeypatch):
    recorded = {}

    async def fake_post_json(_client, url, params=None):
        recorded["url"] = url
        recorded["params"] = params or {}
        return {"status": "rolled_back"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(health, "_post_json", fake_post_json)
    monkeypatch.setattr(health.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        health._execute_demo_fault(
            SimpleNamespace(
                order_service_url="http://localhost:8002",
                user_service_url="http://localhost:8001",
                api_gateway_url="http://localhost:8000",
            ),
            health.DemoFaultRequest(
                fault_type="rollback",
                service="order-service",
                target_version="v0.8.5",
            ),
        )
    )

    assert result["fault_type"] == "rollback"
    assert recorded["url"] == "http://localhost:8002/fault/rollback"
    assert recorded["params"]["target_version"] == "v0.8.5"


def test_execute_demo_fault_rollback_rejects_unsupported_service():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            health._execute_demo_fault(
                SimpleNamespace(
                    order_service_url="http://localhost:8002",
                    user_service_url="http://localhost:8001",
                    api_gateway_url="http://localhost:8000",
                ),
                health.DemoFaultRequest(
                    fault_type="rollback",
                    service="user-service",
                ),
            )
        )

    assert exc.value.status_code == 400


def test_demo_fault_reset_resets_order_and_user_services(monkeypatch):
    async def fake_post_json(_client, url, params=None):
        if "8002" in url:
            return {"status": "reset-order"}
        if "8001" in url:
            return {"status": "reset-user"}
        return {"status": "reset-gateway"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(
            order_service_url="http://localhost:8002",
            user_service_url="http://localhost:8001",
            api_gateway_url="http://localhost:8000",
        ),
    )
    monkeypatch.setattr(health, "_post_json", fake_post_json)
    monkeypatch.setattr(health.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(health.demo_fault_reset(user={"role": "admin"}))

    assert result["reset"] is True
    assert result["services"]["order-service"]["status"] == "reset-order"
    assert result["services"]["user-service"]["status"] == "reset-user"
    assert result["services"]["api-gateway"]["status"] == "reset-gateway"


def test_demo_prepare_scenario_rewarms_and_resets_monitor(monkeypatch):
    calls = []

    async def fake_post_json(_client, url, params=None):
        calls.append(("POST", url, params or {}))
        return {"status": "ok"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            calls.append(("GET", url, {}))
            return SimpleNamespace()

        async def post(self, url, json=None):
            calls.append(("POSTJSON", url, json or {}))
            return SimpleNamespace()

    monitor_resets = []

    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(
            order_service_url="http://localhost:8002",
            user_service_url="http://localhost:8001",
            api_gateway_url="http://localhost:8000",
        ),
    )
    monkeypatch.setattr(health, "_post_json", fake_post_json)
    monkeypatch.setattr(health.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(health.asyncio, "sleep", lambda seconds: asyncio.sleep(0))
    monkeypatch.setattr(
        health,
        "reset_monitor_state",
        lambda clear_history=True: (
            monitor_resets.append(clear_history) or {"reset": True, "clear_history": clear_history}
        ),
    )

    result = asyncio.run(
        health.demo_prepare_scenario(
            health.DemoScenarioPrepareRequest(
                cooldown_seconds=0,
                warm_order_reads=2,
                warm_order_writes=1,
                warm_user_reads=2,
                reset_monitor=True,
                clear_monitor_history=False,
            ),
            user={"role": "admin"},
        )
    )

    assert result["reset"] is True
    assert result["warmed_requests"]["order_reads"] == 2
    assert result["warmed_requests"]["order_writes"] == 1
    assert result["warmed_requests"]["user_reads"] == 2
    assert monitor_resets == [False]


def test_delete_memory_incident_success(monkeypatch):
    monkeypatch.setattr(health, "delete_incident", lambda incident_id: incident_id == "inc-1")

    result = asyncio.run(health.delete_memory_incident("inc-1", user={"role": "admin"}))

    assert result == {"deleted": True, "incident_id": "inc-1"}


def test_delete_memory_incident_not_found(monkeypatch):
    monkeypatch.setattr(health, "delete_incident", lambda incident_id: False)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(health.delete_memory_incident("missing", user={"role": "admin"}))

    assert exc.value.status_code == 404


def test_list_memory_incidents_formats_rows(monkeypatch):
    class FakeQuery:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def all(self):
            return self.rows

    class FakeDB:
        def __init__(self, rows):
            self.rows = rows

        def query(self, _model):
            return FakeQuery(self.rows)

    row = SimpleNamespace(
        incident_id="inc-12345678",
        service="order-service",
        status="resolved",
        outcome="resolved",
        created_at=datetime(2026, 3, 30, 12, 0, 0),
        alert_signature="order-service:memory:critical",
        state_snapshot={
            "postmortem": {
                "root_cause_confirmed": "Memory leak in order-service",
                "time_to_recovery_seconds": 44.0,
            }
        },
    )

    monkeypatch.setattr(
        health,
        "memory_status",
        lambda: {"provider": "chromadb", "collection": "incident_postmortems", "path": "./chroma_db"},
    )

    result = asyncio.run(
        health.list_memory_incidents(
            limit=10,
            user={"role": "viewer"},
            db=FakeDB([row]),
        )
    )

    assert result["memory"]["provider"] == "chromadb"
    assert len(result["incidents"]) == 1
    assert result["incidents"][0]["incident_id"] == "inc-12345678"
    assert (
        result["incidents"][0]["postmortem"]["root_cause_confirmed"]
        == "Memory leak in order-service"
    )
    assert result["incidents"][0]["similarity_score"] is None
    assert result["total"] == 1
    assert result["query"]["mode"] == "browse"


def test_list_memory_incidents_supports_semantic_search(monkeypatch):
    row = SimpleNamespace(
        incident_id="inc-12345678",
        service="order-service",
        status="resolved",
        outcome="resolved",
        created_at=datetime(2026, 3, 30, 12, 0, 0),
        alert_signature="order-service:memory:critical",
        state_snapshot={
            "postmortem": {
                "root_cause_confirmed": "Memory leak in order-service",
                "time_to_recovery_seconds": 44.0,
                "fix_applied": "restart_container",
            }
        },
    )

    class FakeDB:
        def get(self, _model, incident_id):
            return row if incident_id == "inc-12345678" else None

    monkeypatch.setattr(
        health,
        "memory_status",
        lambda: {"provider": "chromadb", "collection": "incident_postmortems", "path": "./chroma_db"},
    )
    monkeypatch.setattr(
        health,
        "search_similar_incidents",
        lambda query, service, top_k: [
            SimpleNamespace(
                incident_id="inc-12345678",
                service=service,
                alert_signature="order-service:memory:critical",
                root_cause="Memory leak in order-service",
                fix_applied="restart_container",
                outcome="resolved",
                time_to_recovery_seconds=44.0,
                similarity_score=0.97,
                occurred_at="2026-03-30T12:00:00",
            )
        ],
    )

    result = asyncio.run(
        health.list_memory_incidents(
            query="memory leak",
            service="order-service",
            top_k=5,
            user={"role": "viewer"},
            db=FakeDB(),
        )
    )

    assert result["query"]["mode"] == "semantic_search"
    assert result["query"]["service"] == "order-service"
    assert result["incidents"][0]["similarity_score"] == 0.97
    assert result["incidents"][0]["postmortem"]["fix_applied"] == "restart_container"


def test_demo_fault_status_aggregates_services(monkeypatch):
    async def fake_fetch_json(_client, name, url):
        return {"name": name, "ok": True, "data": {"url": url}}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        health,
        "get_settings",
        lambda: SimpleNamespace(
            order_service_url="http://localhost:8002",
            user_service_url="http://localhost:8001",
            api_gateway_url="http://localhost:8000",
        ),
    )
    monkeypatch.setattr(health, "_fetch_json", fake_fetch_json)
    monkeypatch.setattr(health.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(health.demo_fault_status(user={"role": "viewer"}))

    assert set(result["services"].keys()) == {"order-service", "user-service", "api-gateway"}
    assert result["services"]["order-service"]["ok"] is True


def test_health_reports_all_checks(monkeypatch):
    class FakeDB:
        def execute(self, _sql):
            return 1

        def close(self):
            return None

    monkeypatch.setattr(health, "get_session_factory", lambda: lambda: FakeDB())
    monkeypatch.setattr(health, "prom_health", lambda: True)
    monkeypatch.setattr(health, "loki_health", lambda: True)
    monkeypatch.setattr(health, "jaeger_health", lambda: True)
    monkeypatch.setattr(health, "incident_memory_health", lambda: True)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "key")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    result = asyncio.run(health.health())

    assert result["status"] == "ok"
    assert result["checks"]["postgres"] is True
    assert result["checks"]["incident_memory"] is True
    assert result["checks"]["langsmith"] is True
    assert isinstance(result["uptime_seconds"], int)


def test_health_degraded_on_database_failure(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_session_factory",
        lambda: lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    monkeypatch.setattr(health, "prom_health", lambda: True)
    monkeypatch.setattr(health, "loki_health", lambda: False)
    monkeypatch.setattr(health, "jaeger_health", lambda: True)
    monkeypatch.setattr(health, "incident_memory_health", lambda: False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    result = asyncio.run(health.health())

    assert result["status"] == "degraded"
    assert result["checks"]["postgres"] is False
    assert result["checks"]["incident_memory"] is False
    assert result["checks"]["loki"] is False


def test_metrics_export_uses_prometheus_payload(monkeypatch):
    monkeypatch.setattr(health, "prometheus_export", lambda: (b"metric 1\n", "text/plain"))

    response = asyncio.run(health.metrics_export(user={"role": "viewer"}))

    assert response.body == b"metric 1\n"
    assert response.media_type == "text/plain"


def test_metrics_summary_reads_all_queries(monkeypatch):
    monkeypatch.setattr(
        health,
        "METRIC_QUERIES",
        {
            "m1": {"service": "order-service", "query": "up"},
            "m2": {"service": "user-service", "query": "down"},
        },
    )
    monkeypatch.setattr(health, "query_instant", lambda query: 1.0 if query == "up" else 2.0)

    result = asyncio.run(health.metrics_summary(user={"role": "viewer"}))

    assert result["metrics"]["m1"]["value"] == 1.0
    assert result["metrics"]["m2"]["service"] == "user-service"
    assert result["metrics"]["m1"]["verifier_query"] is None


def test_observability_summary_formats_metric_status(monkeypatch):
    monkeypatch.setattr(
        health,
        "METRIC_PROFILES",
        {
            "m1": SimpleNamespace(
                service="order-service",
                description="Order metric",
                query="up",
                verifier_query="up",
                threshold_direction="high",
                minimum_samples=2,
                minimum_absolute_delta=0.5,
                minimum_relative_delta=0.1,
                minimum_stddev=0.01,
                baseline_hours=12,
                query_step="5m",
            )
        },
    )
    monkeypatch.setattr(health, "query_instant", lambda query: 3.0)
    monkeypatch.setattr(health, "_query_range", lambda query, hours, step: [1.0, 1.5, 2.0])

    result = asyncio.run(health.observability_summary(user={"role": "viewer"}))

    assert result["metrics"]["m1"]["status"] == "anomalous"
    assert result["metrics"]["m1"]["service"] == "order-service"
    assert result["metrics"]["m1"]["baseline_window_hours"] == 12


def test_monitor_trigger_formats_alert(monkeypatch):
    alert = SimpleNamespace(
        service="order-service",
        metric_name="order_error_rate",
        severity="CRITICAL",
        description="error spike",
    )
    monkeypatch.setattr(health, "initial_state", lambda: {"status": "active"})
    monkeypatch.setattr(
        health, "monitor_agent", lambda state: {"alert": alert, "raw_metrics": {"x": 1}}
    )

    result = asyncio.run(health.monitor_trigger(user={"role": "admin"}))

    assert result["alert_fired"] is True
    assert result["alert"]["metric_name"] == "order_error_rate"


def test_monitor_trigger_handles_no_alert(monkeypatch):
    monkeypatch.setattr(health, "initial_state", lambda: {"status": "active"})
    monkeypatch.setattr(health, "monitor_agent", lambda state: {"alert": None, "raw_metrics": {}})

    result = asyncio.run(health.monitor_trigger(user={"role": "admin"}))

    assert result["alert_fired"] is False
    assert result["alert"] is None


def test_fetch_json_handles_errors():
    class FakeClient:
        async def get(self, url):
            raise RuntimeError("boom")

    result = asyncio.run(health._fetch_json(FakeClient(), "svc", "http://svc"))

    assert result["ok"] is False
    assert result["name"] == "svc"


def test_post_json_normalizes_non_dict_payload():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [1, 2, 3]

    class FakeClient:
        async def post(self, url, params=None):
            return FakeResponse()

    result = asyncio.run(health._post_json(FakeClient(), "http://svc"))

    assert result["raw"] == [1, 2, 3]


def test_execute_demo_fault_rejects_unknown_fault():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            health._execute_demo_fault(
                SimpleNamespace(
                    order_service_url="http://localhost:8002",
                    user_service_url="http://localhost:8001",
                    api_gateway_url="http://localhost:8000",
                ),
                health.DemoFaultRequest(fault_type="unknown"),
            )
        )

    assert exc.value.status_code == 400


def test_execute_demo_fault_memory_leak_and_cascade(monkeypatch):
    calls = []

    async def fake_post_json(_client, url, params=None):
        calls.append((url, params or {}))
        return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(health, "_post_json", fake_post_json)
    monkeypatch.setattr(health.httpx, "AsyncClient", FakeAsyncClient)
    settings = SimpleNamespace(
        order_service_url="http://localhost:8002",
        user_service_url="http://localhost:8001",
        api_gateway_url="http://localhost:8000",
    )

    memory = asyncio.run(
        health._execute_demo_fault(
            settings, health.DemoFaultRequest(fault_type="memory_leak", cycles=2)
        )
    )
    cascade = asyncio.run(
        health._execute_demo_fault(
            settings, health.DemoFaultRequest(fault_type="cascade_failure", cycles=2)
        )
    )

    assert memory["service"] == "order-service"
    assert cascade["service"] == "multi-service"
    assert any("/fault/memory-leak" in call[0] for call in calls)


def test_execute_demo_fault_other_paths(monkeypatch):
    calls = []

    async def fake_post_json(_client, url, params=None):
        calls.append((url, params or {}))
        return {"ok": True}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(health, "_post_json", fake_post_json)
    monkeypatch.setattr(health.httpx, "AsyncClient", FakeAsyncClient)
    settings = SimpleNamespace(
        order_service_url="http://localhost:8002",
        user_service_url="http://localhost:8001",
        api_gateway_url="http://localhost:8000",
    )

    asyncio.run(
        health._execute_demo_fault(
            settings, health.DemoFaultRequest(fault_type="db_exhaustion", connections=101)
        )
    )
    asyncio.run(
        health._execute_demo_fault(
            settings, health.DemoFaultRequest(fault_type="slow_query", delay_seconds=3.2)
        )
    )
    asyncio.run(
        health._execute_demo_fault(
            settings, health.DemoFaultRequest(fault_type="error_rate", rate=2.0)
        )
    )
    asyncio.run(
        health._execute_demo_fault(
            settings, health.DemoFaultRequest(fault_type="redis_overflow", ratio=2.0)
        )
    )

    assert calls[0][1]["connections"] == 100
    assert calls[1][1]["delay_seconds"] == 3.2
    assert calls[2][1]["rate"] == 1.0
    assert calls[3][1]["ratio"] == 1.0


def test_run_cpu_spike_tolerates_request_errors(monkeypatch):
    class FakeLoop:
        def __init__(self):
            self.values = iter([0.0, 0.5, 2.0])

        def time(self):
            return next(self.values)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url):
            raise RuntimeError("transient")

    loop = FakeLoop()
    monkeypatch.setattr(health.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(health.httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(health._run_cpu_spike("http://localhost:8002", 1))
