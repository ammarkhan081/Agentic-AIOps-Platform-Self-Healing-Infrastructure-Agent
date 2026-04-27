"""
API Gateway — FastAPI microservice
Entry point. Routes traffic to User and Order services.
Tracks request routing metrics and distributed traces.
"""
import logging
import json
import random
import time
import os
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import uvicorn

class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "level": record.levelname,
            "message": record.getMessage(),
            "service": "api-gateway",
            "time": self.formatTime(record),
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger("api-gateway")
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

JAEGER_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
provider = TracerProvider()
exporter = OTLPSpanExporter(endpoint=JAEGER_ENDPOINT, insecure=True)
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("api-gateway")

REQUEST_COUNT   = Counter("gateway_requests_total",       "Total gateway requests", ["upstream", "status"])
REQUEST_LATENCY = Histogram("gateway_request_duration_seconds", "Gateway latency", ["upstream"])
ERROR_COUNT     = Counter("gateway_errors_total",          "Gateway errors",       ["upstream", "error_type"])
UPSTREAM_HEALTH = Gauge("gateway_upstream_health", "Upstream service health state", ["upstream"])

USER_SERVICE_URL  = os.getenv("USER_SERVICE_URL",  "http://user-service:8001")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8002")

app = FastAPI(title="API Gateway", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
FastAPIInstrumentor.instrument_app(app)

_gateway_fault_state = {
    "mode": "normal",
    "last_reset_at": "",
}


@app.get("/")
def root():
    return {
        "service": "API Gateway",
        "version": "1.0.0",
        "upstreams": {"user-service": USER_SERVICE_URL, "order-service": ORDER_SERVICE_URL},
    }


@app.get("/health")
async def health():
    results = {}
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, url in [("user-service", USER_SERVICE_URL), ("order-service", ORDER_SERVICE_URL)]:
            try:
                r = await client.get(f"{url}/health")
                results[name] = r.json()
                UPSTREAM_HEALTH.labels(upstream=name).set(1 if results[name].get("status") == "ok" else 0)
            except Exception as e:
                results[name] = {"status": "unreachable", "error": str(e)}
                UPSTREAM_HEALTH.labels(upstream=name).set(0)
    overall = "ok" if all(v.get("status") == "ok" for v in results.values()) else "degraded"
    return {"status": overall, "service": "api-gateway", "upstreams": results}


@app.get("/fault/status")
async def fault_status():
    return {
        "mode": _gateway_fault_state["mode"],
        "last_reset_at": _gateway_fault_state["last_reset_at"],
        "user_service_url": USER_SERVICE_URL,
        "order_service_url": ORDER_SERVICE_URL,
    }


@app.post("/fault/reset")
async def reset_faults():
    _gateway_fault_state["mode"] = "normal"
    _gateway_fault_state["last_reset_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    logger.info("FAULT: API gateway state reset")
    return {"status": "reset", "service": "api-gateway", "mode": _gateway_fault_state["mode"]}


@app.get("/api/users/{user_id}")
async def proxy_get_user(user_id: int):
    start = time.time()
    with tracer.start_as_current_span("proxy_get_user"):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{USER_SERVICE_URL}/users/{user_id}")
            REQUEST_COUNT.labels(upstream="user-service", status=str(r.status_code)).inc()
            REQUEST_LATENCY.labels(upstream="user-service").observe(time.time() - start)
            if r.status_code >= 500:
                ERROR_COUNT.labels(upstream="user-service", error_type="upstream_error").inc()
                raise HTTPException(status_code=r.status_code, detail=r.text)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail=r.text)
            return r.json()
        except HTTPException:
            raise
        except httpx.TimeoutException:
            ERROR_COUNT.labels(upstream="user-service", error_type="timeout").inc()
            raise HTTPException(status_code=504, detail="User service timeout")
        except Exception as e:
            ERROR_COUNT.labels(upstream="user-service", error_type="connection_error").inc()
            raise HTTPException(status_code=502, detail=f"User service error: {str(e)}")


@app.get("/api/orders")
async def proxy_list_orders():
    start = time.time()
    with tracer.start_as_current_span("proxy_list_orders"):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{ORDER_SERVICE_URL}/orders")
            REQUEST_COUNT.labels(upstream="order-service", status=str(r.status_code)).inc()
            REQUEST_LATENCY.labels(upstream="order-service").observe(time.time() - start)
            if r.status_code >= 500:
                ERROR_COUNT.labels(upstream="order-service", error_type="upstream_error").inc()
                raise HTTPException(status_code=r.status_code, detail=r.text)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail=r.text)
            return r.json()
        except HTTPException:
            raise
        except httpx.TimeoutException:
            ERROR_COUNT.labels(upstream="order-service", error_type="timeout").inc()
            raise HTTPException(status_code=504, detail="Order service timeout")
        except Exception as e:
            ERROR_COUNT.labels(upstream="order-service", error_type="connection_error").inc()
            raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/orders")
async def proxy_create_order(request: Request):
    start = time.time()
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    with tracer.start_as_current_span("proxy_create_order"):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(f"{ORDER_SERVICE_URL}/orders", json=body)
            REQUEST_COUNT.labels(upstream="order-service", status=str(r.status_code)).inc()
            REQUEST_LATENCY.labels(upstream="order-service").observe(time.time() - start)
            if r.status_code >= 500:
                ERROR_COUNT.labels(upstream="order-service", error_type="upstream_error").inc()
                raise HTTPException(status_code=r.status_code, detail=r.text)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail=r.text)
            return r.json()
        except HTTPException:
            raise
        except httpx.TimeoutException:
            ERROR_COUNT.labels(upstream="order-service", error_type="timeout").inc()
            raise HTTPException(status_code=504, detail="Order service timeout")
        except Exception as e:
            ERROR_COUNT.labels(upstream="order-service", error_type="connection_error").inc()
            raise HTTPException(status_code=502, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)
