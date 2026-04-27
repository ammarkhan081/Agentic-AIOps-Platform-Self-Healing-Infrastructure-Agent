"""Jaeger trace tool — fetches distributed trace spans for Root Cause Agent."""

import logging
import os
from datetime import datetime, timedelta

import httpx

from ..graph.state import TraceSpan

logger = logging.getLogger("tool.jaeger")
JAEGER_URL = os.getenv("JAEGER_URL", "http://localhost:16686")


def fetch_traces(service: str, limit: int = 20) -> list[TraceSpan]:
    """
    Fetch recent trace spans from Jaeger for the given service.
    Returns list of TraceSpan objects.
    """
    end_us = int(datetime.utcnow().timestamp() * 1e6)
    start_us = int((datetime.utcnow() - timedelta(hours=1)).timestamp() * 1e6)

    try:
        resp = httpx.get(
            f"{JAEGER_URL}/api/traces",
            params={
                "service": service,
                "start": str(start_us),
                "end": str(end_us),
                "limit": str(limit),
            },
            timeout=10.0,
        )
        data = resp.json()
        spans = []
        for trace in data.get("data", [])[:limit]:
            for span in trace.get("spans", [])[:5]:  # max 5 spans per trace
                duration_ms = span.get("duration", 0) / 1000
                tags = {t["key"]: t["value"] for t in span.get("tags", [])}
                status = "error" if tags.get("error") else "ok"
                spans.append(
                    TraceSpan(
                        trace_id=span.get("traceID", ""),
                        span_id=span.get("spanID", ""),
                        operation_name=span.get("operationName", ""),
                        service=service,
                        duration_ms=round(duration_ms, 2),
                        status=status,
                        tags=tags,
                    )
                )
        logger.info(f"Jaeger: fetched {len(spans)} spans for {service}")
        return spans
    except Exception as e:
        logger.warning(f"Jaeger fetch failed for {service}: {e}")
        return []


def health_check() -> bool:
    try:
        r = httpx.get(f"{JAEGER_URL}/", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False
