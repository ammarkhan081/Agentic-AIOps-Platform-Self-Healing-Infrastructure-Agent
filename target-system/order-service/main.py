"""
Order Service - FastAPI microservice
Handles order processing with Redis-backed storage, cache, and pressure simulation.
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone

import redis
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app


class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "level": record.levelname,
                "message": record.getMessage(),
                "service": "order-service",
                "time": self.formatTime(record),
                "module": record.module,
            }
        )


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger("order-service")
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

JAEGER_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
REDIS_PREFIX = os.getenv("ORDER_REDIS_PREFIX", "ashia:orders")

provider = TracerProvider()
exporter = OTLPSpanExporter(endpoint=JAEGER_ENDPOINT, insecure=True)
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("order-service")

REQUEST_COUNT = Counter("order_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram("order_request_duration_seconds", "Request latency", ["endpoint"])
MEMORY_LEAK_SIZE = Gauge("order_memory_leak_bytes", "Bytes held by leak store")
ORDER_QUEUE_SIZE = Gauge("order_queue_size", "Current Redis-backed order queue depth")
REDIS_PRESSURE = Gauge("redis_cache_pressure_ratio", "Redis memory pressure ratio")
REDIS_CACHE_HITS = Counter("order_cache_hits_total", "Redis-backed cache hits", ["endpoint"])
REDIS_CACHE_MISSES = Counter("order_cache_misses_total", "Redis-backed cache misses", ["endpoint"])
ERROR_COUNT = Counter("order_errors_total", "Total errors", ["error_type"])

app = FastAPI(title="Order Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
FastAPIInstrumentor.instrument_app(app)

_leak_store: list[bytes] = []
_slow_query_active = False
_slow_query_delay_seconds = 0.0
_error_rate_override = 0.0
_deployment_version = "v1.0.0"
_simulated_replicas = 1
_fallback_orders: list[dict] = []
_redis_client: redis.Redis | None = None


def _orders_key() -> str:
    return f"{REDIS_PREFIX}:feed"


def _cache_key(limit: int) -> str:
    return f"{REDIS_PREFIX}:cache:list:{limit}"


def _filler_key(index: int) -> str:
    return f"{REDIS_PREFIX}:filler:{index}"


def _build_redis_client() -> redis.Redis | None:
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:
        logger.error("Redis unavailable for order-service: %s", exc)
        return None


def _redis_info() -> dict:
    if _redis_client is None:
        return {}
    try:
        return _redis_client.info("memory")
    except Exception as exc:
        logger.error("Redis info lookup failed: %s", exc)
        return {}


def _current_redis_pressure() -> float:
    info = _redis_info()
    used = float(info.get("used_memory", 0) or 0)
    maxmemory = float(info.get("maxmemory", 0) or 0)
    if maxmemory <= 0:
        return 0.0
    return min(1.0, used / maxmemory)


def _sync_redis_metrics() -> None:
    if _redis_client is not None:
        try:
            ORDER_QUEUE_SIZE.set(_redis_client.llen(_orders_key()))
        except Exception:
            ORDER_QUEUE_SIZE.set(len(_fallback_orders))
        REDIS_PRESSURE.set(_current_redis_pressure())
    else:
        ORDER_QUEUE_SIZE.set(len(_fallback_orders))
        REDIS_PRESSURE.set(0.0)


def _store_order(order: dict) -> None:
    if _redis_client is None:
        _fallback_orders.insert(0, order)
        ORDER_QUEUE_SIZE.set(len(_fallback_orders))
        return
    payload = json.dumps(order)
    _redis_client.lpush(_orders_key(), payload)
    _redis_client.ltrim(_orders_key(), 0, 199)
    _redis_client.delete(_cache_key(10))
    _sync_redis_metrics()


def _list_orders(limit: int) -> list[dict]:
    if _redis_client is None:
        ORDER_QUEUE_SIZE.set(len(_fallback_orders))
        REDIS_CACHE_MISSES.labels(endpoint="/orders").inc()
        return _fallback_orders[:limit]

    cached = _redis_client.get(_cache_key(limit))
    if cached:
        REDIS_CACHE_HITS.labels(endpoint="/orders").inc()
        _sync_redis_metrics()
        return json.loads(cached)

    REDIS_CACHE_MISSES.labels(endpoint="/orders").inc()
    raw_orders = _redis_client.lrange(_orders_key(), 0, max(0, limit - 1))
    orders = [json.loads(item) for item in raw_orders]
    _redis_client.setex(_cache_key(limit), 15, json.dumps(orders))
    _sync_redis_metrics()
    return orders


def _inflate_redis_pressure(target_ratio: float) -> float:
    if _redis_client is None:
        return 0.0
    chunk = "x" * 262144
    target = max(0.0, min(1.0, target_ratio))
    index = 0
    current = _current_redis_pressure()
    while current < target and index < 512:
        _redis_client.set(_filler_key(index), chunk)
        index += 1
        current = _current_redis_pressure()
    _sync_redis_metrics()
    return current


def _clear_redis_fault_keys() -> None:
    if _redis_client is None:
        return
    keys = list(_redis_client.scan_iter(f"{REDIS_PREFIX}:filler:*"))
    if keys:
        _redis_client.delete(*keys)
    _redis_client.delete(_cache_key(10))
    _sync_redis_metrics()


def _seed_orders() -> None:
    if _redis_client is None:
        if not _fallback_orders:
            _fallback_orders.extend(
                {
                    "id": 1000 + idx,
                    "status": "seeded",
                    "item": {"sku": f"seed-{idx}", "quantity": 1},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                for idx in range(1, 6)
            )
        ORDER_QUEUE_SIZE.set(len(_fallback_orders))
        return

    if _redis_client.llen(_orders_key()) > 0:
        _sync_redis_metrics()
        return

    seeded_orders = [
        {
            "id": 1000 + idx,
            "status": "seeded",
            "item": {"sku": f"seed-{idx}", "quantity": 1},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        for idx in range(1, 6)
    ]
    pipe = _redis_client.pipeline()
    for order in reversed(seeded_orders):
        pipe.lpush(_orders_key(), json.dumps(order))
    pipe.ltrim(_orders_key(), 0, 199)
    pipe.execute()
    _redis_client.setex(_cache_key(10), 15, json.dumps(seeded_orders))
    _sync_redis_metrics()


@app.on_event("startup")
def startup() -> None:
    global _redis_client
    _redis_client = _build_redis_client()
    _seed_orders()
    _sync_redis_metrics()


@app.get("/health")
def health():
    redis_status = "ok"
    if _redis_client is None:
        redis_status = "degraded"
    else:
        try:
            _redis_client.ping()
        except Exception as exc:
            redis_status = f"error: {exc}"
    _sync_redis_metrics()
    return {
        "status": "ok" if redis_status == "ok" else "degraded",
        "service": "order-service",
        "version": "1.0.0",
        "deployment_version": _deployment_version,
        "redis_status": redis_status,
        "redis_url": REDIS_URL.rsplit("@", 1)[-1],
        "queue_depth": int(ORDER_QUEUE_SIZE._value.get()),
    }


@app.get("/orders")
def list_orders():
    start = time.time()
    with tracer.start_as_current_span("list_orders"):
        delay = random.uniform(0.01, 0.05)
        if _slow_query_active:
            delay += _slow_query_delay_seconds + random.uniform(0.2, 0.8)
            logger.warning("Slow query active - elevated latency detected")
        time.sleep(delay)

        if random.random() < _error_rate_override:
            ERROR_COUNT.labels(error_type="simulated_500").inc()
            REQUEST_COUNT.labels(method="GET", endpoint="/orders", status="500").inc()
            raise HTTPException(status_code=500, detail="Simulated internal error")

        orders = _list_orders(max(1, min(10 * _simulated_replicas, 50)))
        REQUEST_COUNT.labels(method="GET", endpoint="/orders", status="200").inc()
        REQUEST_LATENCY.labels(endpoint="/orders").observe(time.time() - start)
        return {"orders": orders}


@app.post("/orders")
def create_order(item: dict | None = None):
    start = time.time()
    with tracer.start_as_current_span("create_order"):
        time.sleep(random.uniform(0.02, 0.08))
        order_id = random.randint(10000, 99999)
        order = {
            "id": order_id,
            "status": "created",
            "item": item or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _store_order(order)
        REQUEST_COUNT.labels(method="POST", endpoint="/orders", status="201").inc()
        REQUEST_LATENCY.labels(endpoint="/orders").observe(time.time() - start)
        logger.info("Order created: order_id=%s", order_id)
        return {"order_id": order_id, "status": "created"}


@app.post("/fault/memory-leak")
def inject_memory_leak(mb_per_call: int = 5):
    chunk = os.urandom(max(1, mb_per_call) * 1024 * 1024)
    _leak_store.append(chunk)
    leak_bytes = sum(len(item) for item in _leak_store)
    leak_mb = leak_bytes / (1024 * 1024)
    MEMORY_LEAK_SIZE.set(leak_bytes)
    logger.error("FAULT: Memory leak injected. Total leak: %.1f MB", leak_mb)
    return {"status": "injected", "leak_mb": round(leak_mb, 2)}


@app.post("/fault/slow-query")
def inject_slow_query(active: bool = True, delay_seconds: float = 2.5):
    global _slow_query_active, _slow_query_delay_seconds
    _slow_query_active = active
    _slow_query_delay_seconds = max(0.0, delay_seconds) if active else 0.0
    logger.error("FAULT: Slow query %s", "activated" if active else "deactivated")
    return {
        "status": "slow_query_active" if active else "normal",
        "active": active,
        "delay_seconds": _slow_query_delay_seconds,
    }


@app.post("/fault/error-rate")
def inject_error_rate(rate: float = 0.5):
    global _error_rate_override
    _error_rate_override = max(0.0, min(1.0, rate))
    logger.error("FAULT: Error rate set to %.0f%%", _error_rate_override * 100)
    return {"status": "injected", "error_rate": _error_rate_override}


@app.post("/fault/redis-overflow")
def inject_redis_overflow(ratio: float = 0.95):
    pressure = _inflate_redis_pressure(ratio)
    logger.error("FAULT: Redis pressure driven to %.0f%%", pressure * 100)
    return {"status": "injected", "redis_pressure_ratio": pressure}


@app.post("/fault/rollback")
def rollback_deployment(target_version: str = "v0.9.0"):
    global _deployment_version, _slow_query_active, _slow_query_delay_seconds, _error_rate_override
    previous = _deployment_version
    _deployment_version = target_version
    _slow_query_active = False
    _slow_query_delay_seconds = 0.0
    _error_rate_override = 0.0
    logger.warning("FAULT: Deployment rolled back from %s to %s", previous, _deployment_version)
    return {
        "status": "rolled_back",
        "previous_version": previous,
        "deployment_version": _deployment_version,
    }


@app.post("/fault/scale")
def scale_service(replicas: int = 2):
    global _simulated_replicas
    previous = _simulated_replicas
    _simulated_replicas = max(1, min(10, replicas))
    logger.warning("FAULT: Simulated scale changed from %s to %s replicas", previous, _simulated_replicas)
    return {"status": "scaled", "previous_replicas": previous, "replicas": _simulated_replicas}


@app.post("/fault/config-patch")
def apply_config_patch(mode: str = "stabilize"):
    global _slow_query_active, _slow_query_delay_seconds, _error_rate_override
    if mode == "stabilize":
        _slow_query_active = False
        _slow_query_delay_seconds = 0.0
        _error_rate_override = 0.0
        _clear_redis_fault_keys()
    logger.warning("FAULT: Config patch applied in mode=%s", mode)
    return {
        "status": "patched",
        "mode": mode,
        "slow_query_active": _slow_query_active,
        "error_rate": _error_rate_override,
        "redis_pressure_ratio": _current_redis_pressure(),
    }


@app.post("/fault/reset")
def reset_faults():
    global _leak_store, _slow_query_active, _slow_query_delay_seconds, _error_rate_override
    global _deployment_version, _simulated_replicas, _fallback_orders
    _leak_store = []
    _slow_query_active = False
    _slow_query_delay_seconds = 0.0
    _error_rate_override = 0.0
    _deployment_version = "v1.0.0"
    _simulated_replicas = 1
    _fallback_orders = []
    MEMORY_LEAK_SIZE.set(0)
    if _redis_client is not None:
        keys = list(_redis_client.scan_iter(f"{REDIS_PREFIX}:*"))
        if keys:
            _redis_client.delete(*keys)
    _sync_redis_metrics()
    logger.info("FAULT: All faults reset")
    return {"status": "reset", "message": "All faults cleared"}


@app.get("/fault/status")
def fault_status():
    _sync_redis_metrics()
    return {
        "leak_mb": round(sum(len(item) for item in _leak_store) / (1024 * 1024), 2),
        "slow_query_active": _slow_query_active,
        "slow_query_delay_seconds": _slow_query_delay_seconds,
        "error_rate": _error_rate_override,
        "redis_pressure_ratio": _current_redis_pressure(),
        "deployment_version": _deployment_version,
        "replicas": _simulated_replicas,
        "redis_backed": _redis_client is not None,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002, log_config=None)
