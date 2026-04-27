"""Prometheus API tool — used by Monitor and Verifier agents."""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger("tool.prometheus")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")


def query_instant(promql: str) -> Optional[float]:
    """Execute PromQL instant query. Returns float value or None."""
    try:
        r = httpx.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql}, timeout=5.0)
        data = r.json()
        if data.get("status") == "success" and data["data"]["result"]:
            return float(data["data"]["result"][0]["value"][1])
    except Exception as e:
        logger.warning(f"Prometheus instant query failed '{promql}': {e}")
    return None


def query_range(promql: str, hours: int = 24, step: str = "5m") -> list[float]:
    """Execute PromQL range query. Returns list of float values."""
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)
    try:
        r = httpx.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
            timeout=10.0,
        )
        data = r.json()
        if data.get("status") == "success" and data["data"]["result"]:
            return [float(v[1]) for v in data["data"]["result"][0]["values"] if v[1] != "NaN"]
    except Exception as e:
        logger.warning(f"Prometheus range query failed '{promql}': {e}")
    return []


def health_check() -> bool:
    """Returns True if Prometheus is reachable."""
    try:
        r = httpx.get(f"{PROMETHEUS_URL}/-/healthy", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False
