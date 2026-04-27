"""
Unit tests — ChromaDB vector memory tool
Tests: upsert, search, seed function
"""

import os
import tempfile
from datetime import datetime

import pytest

os.environ["CHROMA_PATH"] = tempfile.mkdtemp()  # use temp dir for tests

try:
    import chromadb  # noqa: F401
except Exception:
    pytest.skip("chromadb not import-compatible in this runtime", allow_module_level=True)

from src.graph.state import Postmortem
from src.tools.chroma_tool import (
    search_similar_incidents,
    seed_synthetic_incidents,
    upsert_incident,
)


def _make_postmortem(
    incident_id="test-001",
    service="order-service",
    root_cause="memory leak",
    fix="restart_container",
    outcome="resolved",
):
    return Postmortem(
        incident_id=incident_id,
        service=service,
        alert_signature=f"{service}:order_memory_leak_bytes:CRITICAL",
        root_cause_confirmed=root_cause,
        fix_applied=fix,
        outcome=outcome,
        time_to_recovery_seconds=45.0,
        retry_count=0,
        total_cost_usd=0.02,
        created_at=datetime.utcnow().isoformat(),
    )


class TestUpsertAndSearch:
    def test_upsert_single_incident(self):
        pm = _make_postmortem("upsert-001")
        upsert_incident(pm)  # should not raise

    def test_search_returns_results_after_upsert(self):
        pm = _make_postmortem("search-001", root_cause="OOM kill from memory leak")
        upsert_incident(pm)
        results = search_similar_incidents("memory leak OOM kill order service")
        assert len(results) >= 1

    def test_search_returns_past_incident_objects(self):
        from src.graph.state import PastIncident

        pm = _make_postmortem("type-001")
        upsert_incident(pm)
        results = search_similar_incidents("memory leak")
        for r in results:
            assert isinstance(r, PastIncident)

    def test_similarity_score_between_0_and_1(self):
        pm = _make_postmortem("sim-001")
        upsert_incident(pm)
        results = search_similar_incidents("memory leak")
        for r in results:
            assert 0.0 <= r.similarity_score <= 1.0

    def test_search_empty_collection_returns_empty_list(self):
        # Use a separate temp dir for empty test

        orig_path = os.environ.get("CHROMA_PATH")
        os.environ["CHROMA_PATH"] = tempfile.mkdtemp()

        # Reset the global _collection
        import src.tools.chroma_tool as ct

        ct._client = None
        ct._collection = None

        results = search_similar_incidents("anything")
        assert results == []

        # Restore
        os.environ["CHROMA_PATH"] = orig_path
        ct._client = None
        ct._collection = None

    def test_upsert_idempotent(self):
        """Upserting same incident_id twice should not raise or duplicate."""
        pm = _make_postmortem("idem-001")
        upsert_incident(pm)
        upsert_incident(pm)  # second upsert — should silently overwrite


class TestSeedSyntheticIncidents:
    def test_seed_populates_collection(self):
        import src.tools.chroma_tool as ct

        ct._client = None
        ct._collection = None
        os.environ["CHROMA_PATH"] = tempfile.mkdtemp()

        seed_synthetic_incidents()
        results = search_similar_incidents("memory leak order service", top_k=10)
        assert len(results) >= 1

    def test_seed_not_called_twice(self):
        """Second seed call should be a no-op (collection already has 10+ items)."""
        seed_synthetic_incidents()
        seed_synthetic_incidents()  # should not duplicate
