"""
Legacy optional Pinecone incident-memory adapter.

The default ASHIA runtime uses local ChromaDB for reproducible demos. This
module is kept as a migration path for hosted vector-memory experiments.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from ..core.config import get_settings
from ..graph.state import PastIncident, Postmortem

logger = logging.getLogger("pinecone-tool")


def _build_embedding_text(incident: Postmortem) -> str:
    return "\n".join(
        [
            f"service={incident.service}",
            f"signature={incident.alert_signature}",
            f"root_cause={incident.root_cause_confirmed}",
            f"fix={incident.fix_applied}",
            f"outcome={incident.outcome}",
            f"retry_count={incident.retry_count}",
            f"time_to_recovery={incident.time_to_recovery_seconds}",
        ]
    )


@lru_cache(maxsize=1)
def _get_clients() -> tuple[Any, Any, str, str]:
    settings = get_settings()
    if (
        not settings.pinecone_api_key
        or not settings.openai_api_key
        or settings.openai_api_key == "your_openai_api_key_here"
    ):
        raise RuntimeError("Pinecone memory requires PINECONE_API_KEY and OPENAI_API_KEY")

    from openai import OpenAI
    from pinecone import Pinecone

    openai_client = OpenAI(api_key=settings.openai_api_key)
    pinecone_client = Pinecone(api_key=settings.pinecone_api_key)
    index = pinecone_client.Index(settings.pinecone_index_name)
    return openai_client, index, settings.openai_embedding_model, settings.pinecone_namespace


def _embed_text(text: str) -> list[float]:
    openai_client, _, embedding_model, _ = _get_clients()
    response = openai_client.embeddings.create(model=embedding_model, input=text)
    return response.data[0].embedding


def upsert_incident(incident: Postmortem) -> None:
    _, index, _, namespace = _get_clients()
    text = _build_embedding_text(incident)
    embedding = _embed_text(text)
    metadata = {
        "incident_id": incident.incident_id,
        "service": incident.service,
        "alert_signature": incident.alert_signature,
        "root_cause_confirmed": incident.root_cause_confirmed,
        "fix_applied": incident.fix_applied,
        "outcome": incident.outcome,
        "time_to_recovery_seconds": incident.time_to_recovery_seconds,
        "retry_count": incident.retry_count,
        "total_cost_usd": incident.total_cost_usd,
        "created_at": incident.created_at,
        "document": text,
    }
    index.upsert(
        vectors=[
            {
                "id": incident.incident_id,
                "values": embedding,
                "metadata": metadata,
            }
        ],
        namespace=namespace,
    )


def search_similar_incidents(query: str, service: str, top_k: int = 3) -> list[PastIncident]:
    try:
        _, index, _, namespace = _get_clients()
    except Exception as exc:
        logger.warning("Pinecone memory unavailable: %s", exc)
        return []

    embedding = _embed_text(f"service={service}\nquery={query}")
    response = index.query(
        vector=embedding,
        top_k=top_k,
        include_metadata=True,
        namespace=namespace,
        filter={"service": {"$eq": service}},
    )

    results: list[PastIncident] = []
    for match in response.matches or []:
        metadata = match.metadata or {}
        results.append(
            PastIncident(
                incident_id=metadata.get("incident_id", str(match.id)),
                service=metadata.get("service", service),
                alert_signature=metadata.get("alert_signature", ""),
                root_cause=metadata.get("root_cause_confirmed", "Unknown"),
                fix_applied=metadata.get("fix_applied", "unknown"),
                outcome=metadata.get("outcome", "unknown"),
                time_to_recovery_seconds=float(metadata.get("time_to_recovery_seconds") or -1.0),
                similarity_score=float(match.score or 0.0),
                occurred_at=metadata.get("created_at", ""),
            )
        )
    return results


def delete_incident(incident_id: str) -> bool:
    try:
        _, index, _, namespace = _get_clients()
        index.delete(ids=[incident_id], namespace=namespace)
        return True
    except Exception as exc:
        logger.warning("Pinecone delete failed for %s: %s", incident_id, exc)
        return False


def memory_status() -> dict[str, str]:
    settings = get_settings()
    return {
        "provider": "pinecone",
        "index": settings.pinecone_index_name,
        "namespace": settings.pinecone_namespace,
    }


def health_check() -> bool:
    """
    Lightweight Pinecone readiness check.
    Returns False when keys/index connectivity are unavailable.
    """
    try:
        _, index, _, namespace = _get_clients()
        # A small stats call validates auth + index reachability.
        index.describe_index_stats(namespace=namespace)
        return True
    except Exception as exc:
        logger.warning("Pinecone health check failed: %s", exc)
        return False


def seed_synthetic_incidents() -> None:
    synthetic: list[dict[str, Any]] = [
        {
            "incident_id": "seed-memory-leak-001",
            "service": "order-service",
            "alert_signature": "order-service:container_memory_usage_bytes:critical",
            "root_cause_confirmed": "Unbounded in-memory list growth caused a memory leak.",
            "fix_applied": "restart ({'service': 'order-service'})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 42.0,
            "retry_count": 0,
            "total_cost_usd": 0.0012,
            "created_at": "2026-01-15T10:00:00",
        },
        {
            "incident_id": "seed-latency-002",
            "service": "api-gateway",
            "alert_signature": "api-gateway:http_request_duration_ms:high",
            "root_cause_confirmed": "Downstream user-service latency caused gateway timeout spikes.",
            "fix_applied": "scale ({'service': 'user-service', 'replicas': 2})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 85.0,
            "retry_count": 1,
            "total_cost_usd": 0.0031,
            "created_at": "2026-01-18T14:30:00",
        },
        {
            "incident_id": "seed-db-003",
            "service": "user-service",
            "alert_signature": "user-service:db_error_rate:critical",
            "root_cause_confirmed": "Database connection pool exhaustion caused request failures.",
            "fix_applied": "config ({'max_connections': 50})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 130.0,
            "retry_count": 1,
            "total_cost_usd": 0.0024,
            "created_at": "2026-01-22T09:10:00",
        },
        {
            "incident_id": "seed-slow-query-004",
            "service": "order-service",
            "alert_signature": "order-service:order_request_latency_p95:high",
            "root_cause_confirmed": "A degraded SQL query plan introduced sustained latency on order reads.",
            "fix_applied": "config_patch ({'service': 'order-service', 'mode': 'stabilize'})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 94.0,
            "retry_count": 1,
            "total_cost_usd": 0.0022,
            "created_at": "2026-01-25T11:45:00",
        },
        {
            "incident_id": "seed-error-rate-005",
            "service": "order-service",
            "alert_signature": "order-service:order_error_rate:critical",
            "root_cause_confirmed": "A bad deploy increased application exceptions on order creation.",
            "fix_applied": "image_rollback ({'service': 'order-service', 'target_version': 'v0.9.0'})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 156.0,
            "retry_count": 2,
            "total_cost_usd": 0.0048,
            "created_at": "2026-01-27T16:00:00",
        },
        {
            "incident_id": "seed-redis-overflow-006",
            "service": "order-service",
            "alert_signature": "order-service:redis_cache_pressure_ratio:high",
            "root_cause_confirmed": "Redis cache pressure reached saturation due to oversized hot-key payloads.",
            "fix_applied": "flush_cache ({'service': 'order-service'})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 33.0,
            "retry_count": 0,
            "total_cost_usd": 0.0011,
            "created_at": "2026-01-29T08:20:00",
        },
        {
            "incident_id": "seed-cpu-spike-007",
            "service": "api-gateway",
            "alert_signature": "api-gateway:gateway_request_rate:high",
            "root_cause_confirmed": "A request surge to the gateway caused upstream saturation and CPU pressure.",
            "fix_applied": "scale_up ({'service': 'order-service', 'replicas': 3})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 74.0,
            "retry_count": 1,
            "total_cost_usd": 0.0027,
            "created_at": "2026-02-01T13:05:00",
        },
        {
            "incident_id": "seed-gateway-errors-008",
            "service": "api-gateway",
            "alert_signature": "api-gateway:gateway_error_rate:high",
            "root_cause_confirmed": "Gateway upstream retries amplified user-service failures into a visible outage.",
            "fix_applied": "db_connection_reset ({'service': 'user-service'})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 118.0,
            "retry_count": 1,
            "total_cost_usd": 0.0035,
            "created_at": "2026-02-03T09:55:00",
        },
        {
            "incident_id": "seed-user-latency-009",
            "service": "user-service",
            "alert_signature": "user-service:user_request_latency_p95:medium",
            "root_cause_confirmed": "Connection pool churn slowed user lookups during a partial dependency outage.",
            "fix_applied": "scale_up ({'service': 'user-service', 'replicas': 2})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 66.0,
            "retry_count": 1,
            "total_cost_usd": 0.0020,
            "created_at": "2026-02-05T15:15:00",
        },
        {
            "incident_id": "seed-db-exhaustion-010",
            "service": "user-service",
            "alert_signature": "user-service:user_db_connections:critical",
            "root_cause_confirmed": "A traffic burst exhausted the user-service DB pool and blocked new requests.",
            "fix_applied": "config_patch ({'service': 'user-service', 'max_connections': 50})",
            "outcome": "resolved",
            "time_to_recovery_seconds": 141.0,
            "retry_count": 2,
            "total_cost_usd": 0.0039,
            "created_at": "2026-02-07T18:10:00",
        },
    ]

    for item in synthetic:
        postmortem = Postmortem(**item)
        upsert_incident(postmortem)

    logger.info("Seeded %s synthetic incidents into Pinecone memory", len(synthetic))


def export_memory_snapshot() -> str:
    sample = memory_status()
    return json.dumps(sample, indent=2)
