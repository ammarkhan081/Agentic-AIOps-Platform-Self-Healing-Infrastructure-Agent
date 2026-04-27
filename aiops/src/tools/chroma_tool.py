"""
ChromaDB Tool — Primary local incident-memory implementation for ASHIA.
Runs entirely on-disk with local embeddings for reproducible demos.
Root Cause Agent queries this to retrieve similar past incidents.
Learning Agent writes postmortems here after every incident close.
"""

import logging
import os
from datetime import datetime
from typing import Optional

from ..graph.state import PastIncident, Postmortem

logger = logging.getLogger("tool.chroma")

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
COLLECTION_NAME = "incident_postmortems"
EMBED_MODEL = "all-MiniLM-L6-v2"  # tiny, fast, no API key, runs locally

_client: Optional[chromadb.PersistentClient] = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

        _client = chromadb.PersistentClient(path=CHROMA_PATH)
        embed_fn = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=embed_fn,
            metadata={"description": "ASHIA incident postmortems for continual learning"},
        )
        logger.info(f"ChromaDB collection ready: {COLLECTION_NAME} ({_collection.count()} items)")
    return _collection


def _postmortem_to_document(pm: Postmortem) -> str:
    """Convert postmortem to a rich text string for embedding."""
    return (
        f"Service: {pm.service}. "
        f"Alert: {pm.alert_signature}. "
        f"Root cause: {pm.root_cause_confirmed}. "
        f"Fix applied: {pm.fix_applied}. "
        f"Outcome: {pm.outcome}. "
        f"Retries: {pm.retry_count}. "
        f"Recovery time: {pm.time_to_recovery_seconds}s."
    )


def upsert_incident(pm: Postmortem) -> None:
    """Write a postmortem into ChromaDB vector store."""
    collection = _get_collection()
    document = _postmortem_to_document(pm)
    metadata = {
        "service": pm.service,
        "alert_signature": pm.alert_signature,
        "root_cause": pm.root_cause_confirmed[:500],
        "fix_applied": pm.fix_applied[:200],
        "outcome": pm.outcome,
        "retry_count": pm.retry_count,
        "time_to_recovery": pm.time_to_recovery_seconds or -1,
        "total_cost_usd": pm.total_cost_usd,
        "created_at": pm.created_at,
    }
    collection.upsert(
        ids=[pm.incident_id],
        documents=[document],
        metadatas=[metadata],
    )
    logger.info(f"ChromaDB: upserted incident {pm.incident_id} — {pm.alert_signature}")


def search_similar_incidents(query: str, service: str = None, top_k: int = 3) -> list[PastIncident]:
    """
    Semantic search for similar past incidents.
    Used by Root Cause Agent before every LLM call.
    """
    collection = _get_collection()
    if collection.count() == 0:
        logger.info("ChromaDB: no incidents in memory yet")
        return []

    where = {"service": service} if service else None
    try:
        results = collection.query(
            query_texts=[query],
            n_results=min(top_k, collection.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        # Fallback without service filter
        results = collection.query(
            query_texts=[query],
            n_results=min(top_k, collection.count()),
            include=["documents", "metadatas", "distances"],
        )

    past = []
    for i, meta in enumerate(results["metadatas"][0]):
        distance = results["distances"][0][i]
        similarity = max(0.0, 1.0 - distance)  # cosine distance → similarity
        past.append(
            PastIncident(
                incident_id=results["ids"][0][i] if results.get("ids") else f"past_{i}",
                service=meta.get("service", "unknown"),
                alert_signature=meta.get("alert_signature", ""),
                root_cause=meta.get("root_cause", ""),
                fix_applied=meta.get("fix_applied", ""),
                outcome=meta.get("outcome", "unknown"),
                time_to_recovery_seconds=float(meta.get("time_to_recovery", -1)),
                similarity_score=round(similarity, 3),
                occurred_at=meta.get("created_at", ""),
            )
        )

    logger.info(f"ChromaDB: found {len(past)} similar incidents for query: {query[:60]}")
    return past


def seed_synthetic_incidents() -> None:
    """
    Pre-populate ChromaDB with 10 synthetic past incidents covering all 6 fault types.
    Called once on startup if collection is empty.
    """
    collection = _get_collection()
    if collection.count() >= 10:
        return

    synthetic = [
        Postmortem(
            "seed_001",
            "order-service",
            "order-service:order_memory_leak_bytes:CRITICAL",
            "Memory leak caused by unbounded list growth in order processing",
            "restart_container ({'container': 'order-service'})",
            "resolved",
            45.0,
            0,
            0.02,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_002",
            "order-service",
            "order-service:order_memory_leak_bytes:HIGH",
            "Gradual memory leak from cached objects not being freed",
            "memory_limit_update ({'container': 'order-service'})",
            "resolved",
            38.0,
            1,
            0.03,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_003",
            "user-service",
            "user-service:user_db_connections:CRITICAL",
            "DB connection pool exhausted by idle connections not being released",
            "db_connection_reset ({'service': 'user-service'})",
            "resolved",
            90.0,
            1,
            0.04,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_004",
            "order-service",
            "order-service:order_request_latency_p95:HIGH",
            "Slow query caused by missing index on orders table",
            "manual_investigation ({'service': 'order-service'})",
            "escalated",
            None,
            2,
            0.05,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_005",
            "order-service",
            "order-service:order_error_rate:CRITICAL",
            "Error rate spike caused by downstream database connection failure",
            "restart_container ({'container': 'order-service'})",
            "resolved",
            55.0,
            0,
            0.02,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_006",
            "api-gateway",
            "api-gateway:gateway_error_rate:HIGH",
            "Gateway error rate elevated due to order-service being unhealthy",
            "restart_container ({'container': 'order-service'})",
            "resolved",
            60.0,
            0,
            0.03,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_007",
            "user-service",
            "user-service:user_request_latency_p95:MEDIUM",
            "Latency spike caused by N+1 query pattern in user listing endpoint",
            "config_patch ({'service': 'user-service', 'fix': 'add pagination'})",
            "escalated",
            None,
            1,
            0.04,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_008",
            "order-service",
            "order-service:order_memory_leak_bytes:HIGH",
            "Memory pressure from large payload processing without streaming",
            "restart_container ({'container': 'order-service'})",
            "resolved",
            42.0,
            0,
            0.02,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_009",
            "user-service",
            "user-service:user_db_connections:HIGH",
            "Connection pool near-exhaustion from long-running transactions",
            "db_connection_reset ({'service': 'user-service'})",
            "resolved",
            75.0,
            1,
            0.03,
            datetime.utcnow().isoformat(),
        ),
        Postmortem(
            "seed_010",
            "order-service",
            "order-service:order_error_rate:HIGH",
            "CPU throttling causing request timeout cascade",
            "restart_container ({'container': 'order-service'})",
            "resolved",
            50.0,
            0,
            0.02,
            datetime.utcnow().isoformat(),
        ),
    ]

    for pm in synthetic:
        upsert_incident(pm)
    logger.info(f"ChromaDB: seeded {len(synthetic)} synthetic incidents")


def delete_incident(incident_id: str) -> bool:
    try:
        collection = _get_collection()
        collection.delete(ids=[incident_id])
        return True
    except Exception as exc:
        logger.warning(f"ChromaDB delete failed for {incident_id}: {exc}")
        return False


def memory_status() -> dict[str, str]:
    return {
        "provider": "chromadb",
        "collection": COLLECTION_NAME,
        "path": CHROMA_PATH,
    }


def health_check() -> bool:
    try:
        collection = _get_collection()
        collection.count()
        return True
    except Exception as exc:
        logger.warning(f"ChromaDB health check failed: {exc}")
        return False


def export_memory_snapshot() -> str:
    import json
    sample = memory_status()
    return json.dumps(sample, indent=2)
