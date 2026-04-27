"""Health and control endpoints for the AIOps control plane."""

from __future__ import annotations

import asyncio
import math
import os
import statistics
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...agents.monitor import _query_range, monitor_agent, reset_monitor_state
from ...core.config import get_settings
from ...core.metrics import control_plane_summary, prometheus_export
from ...db.models import IncidentRecord
from ...db.session import get_db, get_session_factory
from ...graph.state import initial_state
from ...observability.catalog import METRIC_PROFILES, METRIC_QUERIES
from ...tools.jaeger_tool import health_check as jaeger_health
from ...tools.loki_tool import health_check as loki_health
from ...tools.chroma_tool import (
    delete_incident,
    health_check as incident_memory_health,
    memory_status,
    search_similar_incidents,
)
from ...tools.prometheus_tool import health_check as prom_health
from ...tools.prometheus_tool import query_instant
from ..routes.auth import get_current_user, require_role
from ..schemas import (
    ControlPlaneMetricsResponse,
    DeleteMemoryIncidentResponse,
    DemoFaultInjectResponse,
    DemoFaultResetResponse,
    DemoFaultStatusResponse,
    DemoScenarioPrepareResponse,
    HealthResponse,
    MemoryIncidentListResponse,
    MetricsSummaryResponse,
    MonitorTriggerResponse,
    ObservabilitySummaryResponse,
)

router = APIRouter()
STARTED_AT = datetime.now(timezone.utc)


class DemoFaultRequest(BaseModel):
    fault_type: str
    service: str = "order-service"
    cycles: int = 15
    duration: int = 30
    rate: float = 0.7
    ratio: float = 0.95
    connections: int = 95
    delay_seconds: float = 2.5
    replicas: int = 2
    target_version: str = "v0.9.0"


class DemoScenarioPrepareRequest(BaseModel):
    cooldown_seconds: int = 12
    warm_order_reads: int = 12
    warm_order_writes: int = 3
    warm_user_reads: int = 12
    reset_monitor: bool = True
    clear_monitor_history: bool = True


async def _fetch_json(client: httpx.AsyncClient, name: str, url: str) -> dict[str, Any]:
    try:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return {"name": name, "ok": True, "data": payload}
        return {"name": name, "ok": True, "data": {"raw": payload}}
    except Exception as exc:
        return {"name": name, "ok": False, "error": str(exc), "data": {}}


async def _post_json(
    client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await client.post(url, params=params)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {"raw": payload}


def _json_safe_float(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


async def _run_cpu_spike(base_url: str, duration: int) -> None:
    end_time = asyncio.get_running_loop().time() + max(1, duration)
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_running_loop().time() < end_time:
            try:
                await client.post(f"{base_url}/orders")
            except Exception:
                pass


async def _prepare_demo_baseline(
    settings,
    body: DemoScenarioPrepareRequest,
) -> dict[str, Any]:
    cooldown_seconds = max(0, min(body.cooldown_seconds, 120))
    warm_order_reads = max(0, min(body.warm_order_reads, 100))
    warm_order_writes = max(0, min(body.warm_order_writes, 25))
    warm_user_reads = max(0, min(body.warm_user_reads, 100))

    async with httpx.AsyncClient(timeout=5.0) as client:
        order_result = await _post_json(client, f"{settings.order_service_url}/fault/reset")
        user_result = await _post_json(client, f"{settings.user_service_url}/fault/reset")
        gateway_result = await _post_json(client, f"{settings.api_gateway_url}/fault/reset")

        warmed = {"order_reads": 0, "order_writes": 0, "user_reads": 0}

        for _ in range(warm_order_reads):
            try:
                await client.get(f"{settings.api_gateway_url}/api/orders")
                warmed["order_reads"] += 1
            except Exception:
                pass

        for _ in range(warm_order_writes):
            try:
                await client.post(
                    f"{settings.api_gateway_url}/api/orders",
                    json={"sku": "rewarm", "quantity": 1},
                )
                warmed["order_writes"] += 1
            except Exception:
                pass

        for _ in range(warm_user_reads):
            try:
                await client.get(f"{settings.api_gateway_url}/api/users/1")
                warmed["user_reads"] += 1
            except Exception:
                pass

    if cooldown_seconds > 0:
        await asyncio.sleep(cooldown_seconds)

    monitor_result = None
    if body.reset_monitor:
        monitor_result = reset_monitor_state(clear_history=body.clear_monitor_history)

    return {
        "reset": True,
        "reset_monitor": body.reset_monitor,
        "cooldown_seconds": cooldown_seconds,
        "warmed_requests": warmed,
        "services": {
            "order-service": order_result,
            "user-service": user_result,
            "api-gateway": gateway_result,
        },
        "monitor": monitor_result,
    }


async def _execute_demo_fault(settings, body: DemoFaultRequest) -> dict[str, Any]:
    normalized_fault_type = (
        "db_exhaustion" if body.fault_type == "db_connection_exhaustion" else body.fault_type
    )
    allowed_faults = {
        "memory_leak",
        "cpu_spike",
        "db_exhaustion",
        "slow_query",
        "error_rate",
        "redis_overflow",
        "cascade_failure",
        "rollback",
    }
    if normalized_fault_type not in allowed_faults:
        raise HTTPException(status_code=400, detail=f"Unsupported fault type '{body.fault_type}'")

    service_urls = {
        "order-service": settings.order_service_url,
        "user-service": settings.user_service_url,
        "api-gateway": settings.api_gateway_url,
    }

    if body.service not in service_urls and normalized_fault_type != "cascade_failure":
        raise HTTPException(status_code=400, detail=f"Unsupported service '{body.service}'")

    async with httpx.AsyncClient(timeout=5.0) as client:
        if normalized_fault_type == "memory_leak":
            result = []
            for _ in range(max(1, min(body.cycles, 50))):
                result.append(
                    await _post_json(
                        client,
                        f"{service_urls['order-service']}/fault/memory-leak",
                        {"mb_per_call": 5},
                    )
                )
            return {
                "fault_type": body.fault_type,
                "service": "order-service",
                "result": result[-1] if result else {},
            }

        if normalized_fault_type == "db_exhaustion":
            result = await _post_json(
                client,
                f"{service_urls['user-service']}/fault/db-exhaustion",
                {"connections": max(1, min(body.connections, 100))},
            )
            return {"fault_type": body.fault_type, "service": "user-service", "result": result}

        if normalized_fault_type == "slow_query":
            result = await _post_json(
                client,
                f"{service_urls['order-service']}/fault/slow-query",
                {"active": "true", "delay_seconds": max(0.0, body.delay_seconds)},
            )
            return {"fault_type": body.fault_type, "service": "order-service", "result": result}

        if normalized_fault_type == "error_rate":
            result = await _post_json(
                client,
                f"{service_urls['order-service']}/fault/error-rate",
                {"rate": max(0.0, min(body.rate, 1.0))},
            )
            return {"fault_type": body.fault_type, "service": "order-service", "result": result}

        if normalized_fault_type == "redis_overflow":
            result = await _post_json(
                client,
                f"{service_urls['order-service']}/fault/redis-overflow",
                {"ratio": max(0.0, min(body.ratio, 1.0))},
            )
            return {"fault_type": body.fault_type, "service": "order-service", "result": result}

        if normalized_fault_type == "cascade_failure":
            cascade = {
                "memory_leak": [],
                "slow_query": await _post_json(
                    client,
                    f"{service_urls['order-service']}/fault/slow-query",
                    {"active": "true", "delay_seconds": max(0.0, body.delay_seconds)},
                ),
                "redis_overflow": await _post_json(
                    client,
                    f"{service_urls['order-service']}/fault/redis-overflow",
                    {"ratio": max(0.0, min(body.ratio, 1.0))},
                ),
                "db_exhaustion": await _post_json(
                    client,
                    f"{service_urls['user-service']}/fault/db-exhaustion",
                    {"connections": max(1, min(body.connections, 100))},
                ),
            }
            for _ in range(max(1, min(body.cycles, 50))):
                cascade["memory_leak"].append(
                    await _post_json(
                        client,
                        f"{service_urls['order-service']}/fault/memory-leak",
                        {"mb_per_call": 5},
                    )
                )
            return {"fault_type": body.fault_type, "service": "multi-service", "result": cascade}

        if normalized_fault_type == "rollback":
            if body.service != "order-service":
                raise HTTPException(
                    status_code=400,
                    detail="Rollback is only supported for order-service",
                )
            result = await _post_json(
                client,
                f"{service_urls[body.service]}/fault/rollback",
                {"target_version": body.target_version},
            )
            return {"fault_type": body.fault_type, "service": body.service, "result": result}

    return {"fault_type": body.fault_type, "service": body.service, "result": {}}


@router.get("/health", response_model=HealthResponse)
async def health(user=Depends(get_current_user)):
    settings = get_settings()
    # Database readiness check
    db_ok = False
    try:
        db = get_session_factory()()
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        finally:
            db.close()
    except Exception:
        db_ok = False

    # LangSmith check is config/readiness based.
    langsmith_ok = (
        bool(os.getenv("LANGCHAIN_API_KEY", settings.langchain_api_key).strip())
        and os.getenv(
            "LANGCHAIN_TRACING_V2",
            settings.langchain_tracing_v2,
        )
        .strip()
        .lower()
        == "true"
    )

    checks = {
        "prometheus": prom_health(),
        "loki": loki_health(),
        "jaeger": jaeger_health(),
        "postgres": db_ok,
        "incident_memory": incident_memory_health(),
        "langsmith": langsmith_ok,
    }
    overall = "ok" if all(checks.values()) else "degraded"
    uptime_seconds = int((datetime.now(timezone.utc) - STARTED_AT).total_seconds())
    return HealthResponse(
        status=overall,
        service="ashia-aiops",
        version="1.0.0",
        uptime_seconds=uptime_seconds,
        checks=checks,
    ).model_dump()


@router.get("/metrics")
async def metrics_export(user=Depends(get_current_user)):
    payload, content_type = prometheus_export()
    return Response(content=payload, media_type=content_type)


@router.get("/metrics/summary", response_model=MetricsSummaryResponse)
async def metrics_summary(user=Depends(get_current_user)):
    values = {}
    for metric_name, cfg in METRIC_QUERIES.items():
        profile = METRIC_PROFILES.get(metric_name)
        values[metric_name] = {
            "service": cfg["service"],
            "query": cfg["query"],
            "value": _json_safe_float(query_instant(cfg["query"])),
            "verifier_query": profile.verifier_query if profile else None,
            "threshold_direction": profile.threshold_direction
            if profile
            else cfg.get("threshold_direction"),
            "description": profile.description if profile else cfg.get("description"),
            "minimum_samples": profile.minimum_samples if profile else None,
            "minimum_absolute_delta": profile.minimum_absolute_delta if profile else None,
            "minimum_relative_delta": profile.minimum_relative_delta if profile else None,
        }
    return MetricsSummaryResponse(metrics=values).model_dump()


@router.get("/metrics/observability-summary", response_model=ObservabilitySummaryResponse)
async def observability_summary(user=Depends(get_current_user)):
    summary: dict[str, dict[str, Any]] = {}
    for metric_name, profile in METRIC_PROFILES.items():
        current = _json_safe_float(query_instant(profile.query))
        history = _query_range(profile.query, hours=profile.baseline_hours, step=profile.query_step)
        history = [point for point in history if math.isfinite(point)]

        expected_mean = None
        expected_std = None
        z_score = None
        status = "insufficient_history"

        if len(history) >= profile.minimum_samples:
            expected_mean = statistics.mean(history)
            expected_std = statistics.stdev(history) if len(history) > 1 else 0.0
            absolute_delta = (
                abs(current - expected_mean)
                if current is not None and expected_mean is not None
                else 0.0
            )
            relative_delta = absolute_delta / max(abs(expected_mean or 0.0), 1e-9)
            if expected_std >= profile.minimum_stddev and current is not None:
                z_score = _json_safe_float(abs(current - expected_mean) / expected_std)
            direction_matches = current is not None and (
                current < expected_mean
                if profile.threshold_direction == "low"
                else current > expected_mean
            )
            strong_enough = (
                absolute_delta >= profile.minimum_absolute_delta
                and relative_delta >= profile.minimum_relative_delta
            )
            if direction_matches and strong_enough and z_score is not None and z_score >= 2.5:
                status = "anomalous"
            elif direction_matches and strong_enough and expected_std < profile.minimum_stddev:
                status = "anomalous_flat_baseline"
            else:
                status = "healthy"

        summary[metric_name] = {
            "service": profile.service,
            "description": profile.description,
            "query": profile.query,
            "verifier_query": profile.verifier_query,
            "threshold_direction": profile.threshold_direction,
            "current_value": current,
            "expected_mean": _json_safe_float(expected_mean),
            "expected_std": _json_safe_float(expected_std),
            "z_score": _json_safe_float(z_score),
            "minimum_samples": profile.minimum_samples,
            "minimum_absolute_delta": profile.minimum_absolute_delta,
            "minimum_relative_delta": profile.minimum_relative_delta,
            "status": status,
            "baseline_window_hours": profile.baseline_hours,
            "evaluation_step": profile.query_step,
        }

    return ObservabilitySummaryResponse(
        metrics=summary,
        generated_at=datetime.now(timezone.utc).isoformat(),
    ).model_dump()


@router.get("/metrics/control-plane-summary", response_model=ControlPlaneMetricsResponse)
async def metrics_control_plane_summary(user=Depends(get_current_user)):
    return ControlPlaneMetricsResponse(metrics=control_plane_summary()).model_dump()


@router.post("/monitor/trigger", response_model=MonitorTriggerResponse)
async def monitor_trigger(user=Depends(require_role("admin"))):
    state = initial_state()
    result = monitor_agent(state)
    alert = result.get("alert")
    return MonitorTriggerResponse(
        triggered=True,
        alert_fired=bool(alert),
        alert={
            "service": alert.service,
            "metric_name": alert.metric_name,
            "severity": alert.severity,
            "description": alert.description,
        }
        if alert
        else None,
        raw_metrics=result.get("raw_metrics", {}),
    ).model_dump()


@router.get("/memory/incidents", response_model=MemoryIncidentListResponse)
async def list_memory_incidents(
    limit: int = 50,
    query: str | None = None,
    service: str | None = None,
    top_k: int = 10,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    memory = memory_status()
    selected_service = (service or "").strip()
    normalized_top_k = max(1, min(top_k, 50))

    if query and selected_service:
        matches = search_similar_incidents(
            query=query, service=selected_service, top_k=normalized_top_k
        )
        incidents = []
        for match in matches:
            row = db.get(IncidentRecord, match.incident_id)
            postmortem = (row.state_snapshot or {}).get("postmortem") if row else None
            incidents.append(
                {
                    "incident_id": match.incident_id,
                    "service": match.service,
                    "status": row.status if row else match.outcome,
                    "outcome": row.outcome if row and row.outcome else match.outcome,
                    "created_at": (
                        row.created_at.isoformat() if row and row.created_at else match.occurred_at
                    ),
                    "alert_signature": row.alert_signature if row else match.alert_signature,
                    "similarity_score": match.similarity_score,
                    "postmortem": postmortem
                    or {
                        "root_cause_confirmed": match.root_cause,
                        "fix_applied": match.fix_applied,
                        "outcome": match.outcome,
                        "time_to_recovery_seconds": match.time_to_recovery_seconds,
                        "created_at": match.occurred_at,
                        "alert_signature": match.alert_signature,
                    },
                }
            )

        return MemoryIncidentListResponse(
            memory=memory,
            incidents=incidents,
            total=len(incidents),
            query={
                "text": query,
                "service": selected_service,
                "top_k": normalized_top_k,
                "mode": "semantic_search",
            },
        ).model_dump()

    rows = (
        db.query(IncidentRecord)
        .filter(IncidentRecord.outcome.isnot(None))
        .order_by(IncidentRecord.created_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )

    incidents = []
    for row in rows:
        postmortem = (row.state_snapshot or {}).get("postmortem")
        if not postmortem:
            continue
        incidents.append(
            {
                "incident_id": row.incident_id,
                "service": row.service,
                "status": row.status,
                "outcome": row.outcome,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "alert_signature": row.alert_signature,
                "similarity_score": None,
                "postmortem": postmortem,
            }
        )

    return MemoryIncidentListResponse(
        memory=memory,
        incidents=incidents,
        total=len(incidents),
        query={
            "text": query,
            "service": selected_service or None,
            "top_k": normalized_top_k,
            "mode": "browse",
        },
    ).model_dump()


@router.delete("/memory/incidents/{incident_id}", response_model=DeleteMemoryIncidentResponse)
async def delete_memory_incident(
    incident_id: str,
    user=Depends(require_role("admin")),
):
    deleted = delete_incident(incident_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory incident not found or delete failed")
    return DeleteMemoryIncidentResponse(deleted=True, incident_id=incident_id).model_dump()


@router.get("/demo/fault-status", response_model=DemoFaultStatusResponse)
async def demo_fault_status(user=Depends(get_current_user)):
    settings = get_settings()
    async with httpx.AsyncClient(timeout=4.0) as client:
        order_service = await _fetch_json(
            client, "order-service", f"{settings.order_service_url}/fault/status"
        )
        user_service = await _fetch_json(
            client, "user-service", f"{settings.user_service_url}/fault/status"
        )
        api_gateway = await _fetch_json(
            client, "api-gateway", f"{settings.api_gateway_url}/fault/status"
        )

    return DemoFaultStatusResponse(
        services={
            "order-service": order_service,
            "user-service": user_service,
            "api-gateway": api_gateway,
        }
    ).model_dump()


@router.post("/demo/fault-inject", response_model=DemoFaultInjectResponse)
async def demo_fault_inject(
    body: DemoFaultRequest,
    background_tasks: BackgroundTasks,
    user=Depends(require_role("admin", "sre")),
):
    settings = get_settings()
    if body.fault_type == "cpu_spike":
        background_tasks.add_task(_run_cpu_spike, settings.order_service_url, body.duration)
        return DemoFaultInjectResponse(
            queued=True,
            fault_type=body.fault_type,
            service="order-service",
            message=f"CPU spike started for {max(1, body.duration)} seconds",
        ).model_dump()

    result = await _execute_demo_fault(settings, body)
    return DemoFaultInjectResponse(queued=False, **result).model_dump()


@router.post("/demo/fault-reset", response_model=DemoFaultResetResponse)
async def demo_fault_reset(user=Depends(require_role("admin", "sre"))):
    settings = get_settings()
    async with httpx.AsyncClient(timeout=5.0) as client:
        order_result = await _post_json(client, f"{settings.order_service_url}/fault/reset")
        user_result = await _post_json(client, f"{settings.user_service_url}/fault/reset")
        gateway_result = await _post_json(client, f"{settings.api_gateway_url}/fault/reset")

    return DemoFaultResetResponse(
        reset=True,
        services={
            "order-service": order_result,
            "user-service": user_result,
            "api-gateway": gateway_result,
        },
    ).model_dump()


@router.post("/demo/prepare-scenario", response_model=DemoScenarioPrepareResponse)
async def demo_prepare_scenario(
    body: DemoScenarioPrepareRequest,
    user=Depends(require_role("admin", "sre")),
):
    settings = get_settings()
    result = await _prepare_demo_baseline(settings, body)
    return DemoScenarioPrepareResponse(**result).model_dump()
