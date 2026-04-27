"""Loki log tool — fetches recent structured log lines for Root Cause Agent."""

import logging
import os
from datetime import datetime, timedelta

import httpx

from ..graph.state import LogLine

logger = logging.getLogger("tool.loki")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")


def fetch_logs(service: str, limit: int = 50, hours: int = 1) -> list[LogLine]:
    """
    Fetch recent log lines from Loki for the given service.
    Returns list of LogLine objects sorted newest-first.
    """
    end_ns = int(datetime.utcnow().timestamp() * 1e9)
    start_ns = int((datetime.utcnow() - timedelta(hours=hours)).timestamp() * 1e9)
    query = f'{{service="{service}"}}'

    try:
        resp = httpx.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": query,
                "start": str(start_ns),
                "end": str(end_ns),
                "limit": str(limit),
                "direction": "backward",
            },
            timeout=10.0,
        )
        data = resp.json()
        lines = []
        for stream in data.get("data", {}).get("result", []):
            for ts, msg in stream.get("values", []):
                try:
                    import json as _json

                    parsed = _json.loads(msg)
                    lines.append(
                        LogLine(
                            timestamp=parsed.get("time", ts),
                            level=parsed.get("level", "INFO"),
                            message=parsed.get("message", msg),
                            service=parsed.get("service", service),
                            raw=msg,
                        )
                    )
                except Exception:
                    lines.append(
                        LogLine(
                            timestamp=ts,
                            level="INFO",
                            message=msg,
                            service=service,
                            raw=msg,
                        )
                    )
        logger.info(f"Loki: fetched {len(lines)} log lines for {service}")
        return lines[:limit]
    except Exception as e:
        logger.warning(f"Loki fetch failed for {service}: {e}")
        return []


def health_check() -> bool:
    try:
        r = httpx.get(f"{LOKI_URL}/ready", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False
