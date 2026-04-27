"""
Unit tests for local memory adapters and DB session helpers.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace

from src.graph.state import Postmortem


def _build_postmortem(incident_id: str = "inc-1") -> Postmortem:
    return Postmortem(
        incident_id=incident_id,
        service="order-service",
        alert_signature="order-service:memory:critical",
        root_cause_confirmed="memory leak",
        fix_applied="restart_container",
        outcome="resolved",
        time_to_recovery_seconds=42.0,
        retry_count=1,
        total_cost_usd=0.02,
        created_at=datetime.utcnow().isoformat(),
    )


def test_db_session_helpers_cache_engine_and_close_generator(monkeypatch):
    session = importlib.import_module("src.db.session")
    created = []
    closed = []

    monkeypatch.setattr(session, "_engine", None)
    monkeypatch.setattr(session, "_session_factory", None)
    monkeypatch.setattr(
        session,
        "create_engine",
        lambda url, pool_pre_ping=True: created.append((url, pool_pre_ping)) or "engine",
    )
    monkeypatch.setattr(
        session,
        "sessionmaker",
        lambda **kwargs: (
            lambda: SimpleNamespace(close=lambda: closed.append("closed"), kwargs=kwargs)
        ),
    )

    engine1 = session.get_engine()
    engine2 = session.get_engine()
    factory = session.get_session_factory()
    db_gen = session.get_db()
    next(db_gen)
    try:
        next(db_gen)
    except StopIteration:
        pass

    assert engine1 == engine2 == "engine"
    assert created == [(session.DATABASE_URL, True)]
    assert factory().kwargs["bind"] == "engine"
    assert closed == ["closed"]


def test_pinecone_upsert_health_and_seed(monkeypatch):
    pinecone_tool = importlib.import_module("src.tools.pinecone_tool")
    upserts = []
    embeds = []

    class FakeIndex:
        def upsert(self, vectors, namespace):
            upserts.append((vectors, namespace))

        def describe_index_stats(self, namespace=None):
            return {"namespaces": {namespace: {}}}

    monkeypatch.setattr(
        pinecone_tool,
        "_get_clients",
        lambda: (SimpleNamespace(), FakeIndex(), "text-embedding", "production"),
    )
    monkeypatch.setattr(
        pinecone_tool, "_embed_text", lambda text: embeds.append(text) or [0.1, 0.2, 0.3]
    )

    pinecone_tool.upsert_incident(_build_postmortem())
    pinecone_tool.seed_synthetic_incidents()

    assert pinecone_tool.health_check() is True
    assert upserts[0][1] == "production"
    assert "service=order-service" in embeds[0]
    assert len(upserts) == 11


def test_pinecone_search_and_delete_success(monkeypatch):
    pinecone_tool = importlib.import_module("src.tools.pinecone_tool")
    deleted = []

    class FakeMatch:
        def __init__(self):
            self.id = "inc-1"
            self.score = 0.88
            self.metadata = {
                "incident_id": "inc-1",
                "service": "order-service",
                "alert_signature": "order-service:memory:critical",
                "root_cause_confirmed": "memory leak",
                "fix_applied": "restart_container",
                "outcome": "resolved",
                "time_to_recovery_seconds": 42,
                "created_at": "2026-03-30T10:00:00",
            }

    class FakeIndex:
        def query(self, **kwargs):
            return SimpleNamespace(matches=[FakeMatch()])

        def delete(self, ids, namespace):
            deleted.append((ids, namespace))

    monkeypatch.setattr(
        pinecone_tool,
        "_get_clients",
        lambda: (SimpleNamespace(), FakeIndex(), "text-embedding", "production"),
    )
    monkeypatch.setattr(pinecone_tool, "_embed_text", lambda text: [0.1, 0.2, 0.3])

    results = pinecone_tool.search_similar_incidents("memory leak", "order-service")
    removed = pinecone_tool.delete_incident("inc-1")

    assert results[0].incident_id == "inc-1"
    assert results[0].similarity_score == 0.88
    assert removed is True
    assert deleted == [(["inc-1"], "production")]


def test_chroma_tool_upsert_search_and_seed(monkeypatch):
    sys.modules.pop("src.tools.chroma_tool", None)
    chromadb_mod = ModuleType("chromadb")
    embedding_mod = ModuleType("chromadb.utils.embedding_functions")
    collections = []

    class FakeCollection:
        def __init__(self):
            self.items = {}

        def count(self):
            return len(self.items)

        def upsert(self, ids, documents, metadatas):
            for item_id, document, metadata in zip(ids, documents, metadatas):
                self.items[item_id] = {
                    "document": document,
                    "metadata": metadata,
                }

        def query(self, query_texts, n_results, where=None, include=None):
            if where and where.get("service") == "broken":
                raise RuntimeError("force fallback")
            matches = []
            for item_id, payload in list(self.items.items())[:n_results]:
                metadata = payload["metadata"]
                if where and metadata.get("service") != where["service"]:
                    continue
                matches.append((item_id, metadata))
            return {
                "ids": [[item_id for item_id, _ in matches]],
                "metadatas": [[meta for _, meta in matches]],
                "distances": [[0.1 for _ in matches]],
            }

    class FakeClient:
        def __init__(self, path):
            self.path = path
            self.collection = FakeCollection()
            collections.append(self.collection)

        def get_or_create_collection(self, name, embedding_function=None, metadata=None):
            return self.collection

    chromadb_mod.PersistentClient = FakeClient
    embedding_mod.SentenceTransformerEmbeddingFunction = lambda model_name: {
        "model_name": model_name
    }
    sys.modules["chromadb"] = chromadb_mod
    sys.modules["chromadb.utils.embedding_functions"] = embedding_mod

    chroma_tool = importlib.import_module("src.tools.chroma_tool")
    chroma_tool._client = None
    chroma_tool._collection = None

    chroma_tool.upsert_incident(_build_postmortem())
    results = chroma_tool.search_similar_incidents("memory leak", "order-service")
    fallback = chroma_tool.search_similar_incidents("memory leak", "broken")
    chroma_tool._collection = FakeCollection()
    chroma_tool.seed_synthetic_incidents()

    assert results[0].service == "order-service"
    assert fallback[0].service == "order-service"
    assert chroma_tool._collection.count() == 10
    assert "Root cause: memory leak." in chroma_tool._postmortem_to_document(_build_postmortem())
