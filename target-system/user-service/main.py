"""
User Service - FastAPI microservice
Handles user reads backed by PostgreSQL with fault injection for pool exhaustion.
"""

import json
import logging
import os
import random
import time
from contextlib import contextmanager
from typing import Iterator

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from psycopg2 import pool


class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "level": record.levelname,
                "message": record.getMessage(),
                "service": "user-service",
                "time": self.formatTime(record),
            }
        )


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger("user-service")
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

JAEGER_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://ashia:ashia_secret@postgres:5432/ashia_db"
)
USER_DB_POOL_MIN = int(os.getenv("USER_DB_POOL_MIN", "1"))
USER_DB_POOL_MAX = int(os.getenv("USER_DB_POOL_MAX", "20"))

provider = TracerProvider()
exporter = OTLPSpanExporter(endpoint=JAEGER_ENDPOINT, insecure=True)
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("user-service")

REQUEST_COUNT = Counter("user_requests_total", "Total requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram("user_request_duration_seconds", "Latency", ["endpoint"])
DB_CONNECTIONS = Gauge("user_db_connections_active", "Active DB connections")
DB_QUERY_LATENCY = Histogram("user_db_query_duration_seconds", "Database query latency", ["query"])
ERROR_COUNT = Counter("user_errors_total", "Total errors", ["error_type"])

app = FastAPI(title="User Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
FastAPIInstrumentor.instrument_app(app)

_db_pool: pool.SimpleConnectionPool | None = None
_db_exhaustion_active = False
_reserved_fault_connections: list = []
_configured_pool_max = USER_DB_POOL_MAX
_simulated_replicas = 1


def _update_db_gauge() -> None:
    active = len(_reserved_fault_connections)
    if _db_pool is not None:
        active += len(getattr(_db_pool, "_used", {}))
    DB_CONNECTIONS.set(active)


def _build_pool(max_connections: int) -> pool.SimpleConnectionPool:
    maxconn = max(2, min(100, max_connections))
    minconn = min(USER_DB_POOL_MIN, maxconn)
    return pool.SimpleConnectionPool(minconn=minconn, maxconn=maxconn, dsn=DATABASE_URL)


def _close_pool() -> None:
    global _db_pool
    if _db_pool is not None:
        _db_pool.closeall()
        _db_pool = None


def _reset_pool(max_connections: int) -> None:
    global _db_pool, _configured_pool_max
    _release_reserved_connections()
    _close_pool()
    _configured_pool_max = max(2, min(100, max_connections))
    _db_pool = _build_pool(_configured_pool_max)
    _update_db_gauge()


def _release_reserved_connections() -> None:
    global _reserved_fault_connections
    if _db_pool is None:
        _reserved_fault_connections = []
        DB_CONNECTIONS.set(0)
        return
    for connection in _reserved_fault_connections:
        try:
            _db_pool.putconn(connection)
        except Exception:
            pass
    _reserved_fault_connections = []
    _update_db_gauge()


def _ensure_seed_data() -> None:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS app_users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    tier TEXT NOT NULL DEFAULT 'standard'
                )
                """
            )
            cursor.execute("SELECT COUNT(*) FROM app_users")
            count = cursor.fetchone()[0]
            if count == 0:
                cursor.executemany(
                    """
                    INSERT INTO app_users (id, name, email, tier)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    [
                        (idx, f"User {idx}", f"user{idx}@example.com", "standard")
                        for idx in range(1, 51)
                    ],
                )
        connection.commit()


@contextmanager
def db_connection() -> Iterator:
    if _db_pool is None:
        raise RuntimeError("Database pool is not initialized")
    connection = _db_pool.getconn()
    _update_db_gauge()
    try:
        yield connection
    finally:
        _db_pool.putconn(connection)
        _update_db_gauge()


def _count_users(limit: int) -> list[dict]:
    query_started = time.time()
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, email, tier FROM app_users ORDER BY id ASC LIMIT %s",
                (limit,),
            )
            rows = cursor.fetchall()
    DB_QUERY_LATENCY.labels(query="list_users").observe(time.time() - query_started)
    return [
        {"id": row[0], "name": row[1], "email": row[2], "tier": row[3]}
        for row in rows
    ]


def _fetch_user(user_id: int) -> dict | None:
    query_started = time.time()
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, email, tier FROM app_users WHERE id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
    DB_QUERY_LATENCY.labels(query="get_user").observe(time.time() - query_started)
    if row is None:
        return None
    return {"user_id": row[0], "name": row[1], "email": row[2], "tier": row[3]}


@app.on_event("startup")
def startup() -> None:
    try:
        _reset_pool(_configured_pool_max)
        _ensure_seed_data()
        logger.info("PostgreSQL pool initialized for user-service")
    except Exception as exc:
        logger.error("User-service database initialization failed: %s", exc)


@app.on_event("shutdown")
def shutdown() -> None:
    _release_reserved_connections()
    _close_pool()


@app.get("/health")
def health():
    db_status = "ok"
    try:
        with db_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
    except Exception as exc:
        db_status = f"error: {exc}"
    overall = "ok" if db_status == "ok" else "degraded"
    return {
        "status": overall,
        "service": "user-service",
        "version": "1.0.0",
        "database_url": DATABASE_URL.rsplit("@", 1)[-1],
        "db_status": db_status,
        "pool_max": _configured_pool_max,
        "seeded_users": 50,
    }


@app.get("/users/{user_id}")
def get_user(user_id: int):
    start = time.time()
    with tracer.start_as_current_span("get_user"):
        try:
            if _db_exhaustion_active:
                time.sleep(0.03)
            user = _fetch_user(user_id)
            if user is None:
                REQUEST_COUNT.labels(method="GET", endpoint="/users/{user_id}", status="404").inc()
                raise HTTPException(status_code=404, detail="User not found")
            time.sleep(random.uniform(0.005, 0.02))
            REQUEST_COUNT.labels(method="GET", endpoint="/users/{user_id}", status="200").inc()
            REQUEST_LATENCY.labels(endpoint="/users/{user_id}").observe(time.time() - start)
            logger.info("User fetched: user_id=%s", user_id)
            return user
        except HTTPException:
            raise
        except Exception as exc:
            ERROR_COUNT.labels(error_type="db_connection_exhausted").inc()
            REQUEST_COUNT.labels(method="GET", endpoint="/users/{user_id}", status="503").inc()
            logger.error("Database access failed for user_id=%s: %s", user_id, exc)
            raise HTTPException(status_code=503, detail="Database connection pool exhausted") from exc


@app.get("/users")
def list_users(limit: int = 10):
    start = time.time()
    with tracer.start_as_current_span("list_users"):
        try:
            users = _count_users(max(1, min(limit, 100)))
            time.sleep(random.uniform(0.01, 0.03))
            REQUEST_COUNT.labels(method="GET", endpoint="/users", status="200").inc()
            REQUEST_LATENCY.labels(endpoint="/users").observe(time.time() - start)
            return {"users": users}
        except Exception as exc:
            ERROR_COUNT.labels(error_type="db_connection_exhausted").inc()
            REQUEST_COUNT.labels(method="GET", endpoint="/users", status="503").inc()
            logger.error("User listing failed: %s", exc)
            raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.post("/fault/db-exhaustion")
def inject_db_exhaustion(connections: int = 95):
    global _db_exhaustion_active, _reserved_fault_connections
    if _db_pool is None:
        raise HTTPException(status_code=503, detail="Database pool unavailable")

    _release_reserved_connections()
    reserve_target = max(1, min(connections, _configured_pool_max))
    reserved = []
    try:
        for _ in range(reserve_target):
            reserved.append(_db_pool.getconn())
    except Exception:
        pass

    _reserved_fault_connections = reserved
    _db_exhaustion_active = True
    _update_db_gauge()
    logger.error(
        "FAULT: DB exhaustion injected. Reserved %s/%s pool connections",
        len(_reserved_fault_connections),
        _configured_pool_max,
    )
    return {
        "status": "injected",
        "connections": len(_reserved_fault_connections),
        "pool_max": _configured_pool_max,
    }


@app.post("/fault/scale")
def scale_service(replicas: int = 2):
    global _simulated_replicas
    previous = _simulated_replicas
    _simulated_replicas = max(1, min(10, replicas))
    if _db_exhaustion_active and _reserved_fault_connections:
        release_count = min(
            len(_reserved_fault_connections),
            max(1, len(_reserved_fault_connections) // _simulated_replicas),
        )
        for _ in range(release_count):
            connection = _reserved_fault_connections.pop()
            _db_pool.putconn(connection)
    _update_db_gauge()
    logger.warning(
        "FAULT: User service scale changed from %s to %s replicas",
        previous,
        _simulated_replicas,
    )
    return {
        "status": "scaled",
        "previous_replicas": previous,
        "replicas": _simulated_replicas,
        "reserved_connections": len(_reserved_fault_connections),
    }


@app.post("/fault/config-patch")
def apply_config_patch(max_connections: int = 50):
    global _db_exhaustion_active
    _db_exhaustion_active = False
    _reset_pool(max_connections)
    _ensure_seed_data()
    logger.warning("FAULT: DB config patch applied. pool_max=%s", _configured_pool_max)
    return {"status": "patched", "max_connections": _configured_pool_max}


@app.post("/fault/reset")
def reset_faults():
    global _db_exhaustion_active, _simulated_replicas
    _db_exhaustion_active = False
    _simulated_replicas = 1
    _reset_pool(USER_DB_POOL_MAX)
    _ensure_seed_data()
    logger.info("FAULT: DB exhaustion fault reset")
    return {"status": "reset"}


@app.get("/fault/status")
def fault_status():
    return {
        "db_exhaustion_active": _db_exhaustion_active,
        "reserved_connections": len(_reserved_fault_connections),
        "pool_max": _configured_pool_max,
        "replicas": _simulated_replicas,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_config=None)
