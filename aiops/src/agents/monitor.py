"""
Monitor Agent - Agent 01: Perception
Polls Prometheus across key metrics, runs Z-score anomaly detection, and emits alerts.
Includes resilience for observability outages with retry/backoff and meta-alerting.
"""

from __future__ import annotations

import logging
import os
import statistics
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

import httpx

from ..graph.state import AIOpsState, AlertEvent
from ..observability.catalog import METRIC_PROFILES, METRIC_QUERIES

logger = logging.getLogger("monitor-agent")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
ZSCORE_THRESHOLD = float(os.getenv("ANOMALY_ZSCORE_THRESHOLD", "2.5"))
CONSECUTIVE_REQUIRED = int(os.getenv("ANOMALY_CONSECUTIVE_READINGS", "3"))
PROMETHEUS_QUERY_RETRIES = int(os.getenv("PROMETHEUS_QUERY_RETRIES", "3"))
PROMETHEUS_FAILURE_ALERT_RUNS = int(os.getenv("PROMETHEUS_FAILURE_ALERT_RUNS", "3"))
PROMETHEUS_BACKOFF_BASE_SECONDS = float(os.getenv("PROMETHEUS_BACKOFF_BASE_SECONDS", "0.05"))

_consecutive_counts: dict[str, int] = {k: 0 for k in METRIC_QUERIES}
_metric_history: dict[str, list[float]] = {k: [] for k in METRIC_QUERIES}
_prometheus_consecutive_fail_runs = 0


def reset_monitor_state(clear_history: bool = True) -> dict[str, object]:
    """Reset monitor counters so consecutive demo runs start from a clean slate."""
    global _prometheus_consecutive_fail_runs

    for metric_name in _consecutive_counts:
        _consecutive_counts[metric_name] = 0
    if clear_history:
        for metric_name in _metric_history:
            _metric_history[metric_name] = []
    _prometheus_consecutive_fail_runs = 0
    logger.info("Monitor Agent: state reset (clear_history=%s)", clear_history)
    return {
        "reset": True,
        "clear_history": clear_history,
        "tracked_metrics": len(METRIC_QUERIES),
    }


def _query_prometheus(query: str) -> Optional[float]:
    """Execute PromQL instant query with retry/backoff. Returns float or None."""
    for attempt in range(PROMETHEUS_QUERY_RETRIES):
        try:
            resp = httpx.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query},
                timeout=5.0,
            )
            data = resp.json()
            if data.get("status") == "success":
                results = data["data"].get("result", [])
                if results:
                    return float(results[0]["value"][1])
        except Exception as exc:
            logger.warning(
                "Prometheus instant query attempt %s failed for '%s': %s", attempt + 1, query, exc
            )
        if attempt < PROMETHEUS_QUERY_RETRIES - 1:
            time.sleep(PROMETHEUS_BACKOFF_BASE_SECONDS * (2**attempt))
    return None


def _query_range(query: str, hours: int = 24, step: str = "5m") -> list[float]:
    """Fetch a range of values for Z-score baseline calculation with retry/backoff."""
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)

    for attempt in range(PROMETHEUS_QUERY_RETRIES):
        try:
            resp = httpx.get(
                f"{PROMETHEUS_URL}/api/v1/query_range",
                params={
                    "query": query,
                    "start": start.timestamp(),
                    "end": end.timestamp(),
                    "step": step,
                },
                timeout=10.0,
            )
            data = resp.json()
            if data.get("status") == "success":
                results = data["data"].get("result", [])
                if results:
                    return [float(v[1]) for v in results[0].get("values", []) if v[1] != "NaN"]
        except Exception as exc:
            logger.warning(
                "Prometheus range query attempt %s failed for '%s': %s", attempt + 1, query, exc
            )
        if attempt < PROMETHEUS_QUERY_RETRIES - 1:
            time.sleep(PROMETHEUS_BACKOFF_BASE_SECONDS * (2**attempt))
    return []


def _classify_severity(z_score: float) -> str:
    if z_score >= 5.0:
        return "CRITICAL"
    if z_score >= 3.5:
        return "HIGH"
    if z_score >= 2.5:
        return "MEDIUM"
    return "LOW"


def _deviation_matches_direction(direction: str, current_value: float, mean: float) -> bool:
    if direction == "low":
        return current_value < mean
    return current_value > mean


def _passes_minimum_delta(metric_name: str, current_value: float, mean: float) -> bool:
    profile = METRIC_PROFILES[metric_name]
    absolute_delta = abs(current_value - mean)
    relative_delta = absolute_delta / max(abs(mean), 1e-9)
    return absolute_delta >= profile.minimum_absolute_delta and (
        relative_delta >= profile.minimum_relative_delta
    )


def _fallback_severity_for_flat_baseline(
    metric_name: str, current_value: float, mean: float
) -> str:
    profile = METRIC_PROFILES[metric_name]
    absolute_delta = abs(current_value - mean)
    relative_delta = absolute_delta / max(abs(mean), 1e-9)
    if absolute_delta >= max(
        profile.minimum_absolute_delta * 2, profile.minimum_absolute_delta
    ) and relative_delta >= max(profile.minimum_relative_delta * 2, 1.0):
        return "CRITICAL"
    if (
        absolute_delta >= profile.minimum_absolute_delta
        and relative_delta >= profile.minimum_relative_delta
    ):
        return "HIGH"
    return "LOW"


def _snapshot_all_metrics_with_health() -> tuple[dict, int]:
    snapshot: dict[str, float] = {}
    failed = 0
    for name, config in METRIC_QUERIES.items():
        val = _query_prometheus(config["query"])
        if val is None:
            failed += 1
        snapshot[name] = val if val is not None else 0.0
    return snapshot, failed


def _snapshot_all_metrics() -> dict:
    """Backward-compatible helper used by tests."""
    snapshot, _ = _snapshot_all_metrics_with_health()
    return snapshot


def _meta_alert_for_prometheus_failure() -> AlertEvent:
    return AlertEvent(
        alert_id=str(uuid.uuid4())[:8],
        service="observability-stack",
        metric_name="prometheus_unavailable",
        current_value=1.0,
        expected_mean=0.0,
        expected_std=1.0,
        threshold=1.0,
        severity="HIGH",
        fired_at=datetime.utcnow().isoformat(),
        description=(
            "Prometheus API unavailable for 3 consecutive monitor cycles. "
            "Observability stack degraded; escalating to HITL."
        ),
    )


def monitor_agent(state: AIOpsState) -> AIOpsState:
    """
    Monitor Agent node.
    Reads: nothing (polls external Prometheus)
    Writes: alert, raw_metrics, status
    """
    global _prometheus_consecutive_fail_runs

    if state.get("alert"):
        logger.info("Monitor Agent: alert already present in state, skipping poll")
        return state

    logger.info("Monitor Agent: polling Prometheus...")
    snapshot, failed_queries = _snapshot_all_metrics_with_health()

    if failed_queries == len(METRIC_QUERIES):
        _prometheus_consecutive_fail_runs += 1
        logger.warning(
            "Monitor Agent: all Prometheus queries failed (%s/%s consecutive runs)",
            _prometheus_consecutive_fail_runs,
            PROMETHEUS_FAILURE_ALERT_RUNS,
        )
    else:
        _prometheus_consecutive_fail_runs = 0

    if _prometheus_consecutive_fail_runs >= PROMETHEUS_FAILURE_ALERT_RUNS:
        alert = _meta_alert_for_prometheus_failure()
        return {
            **state,
            "raw_metrics": snapshot,
            "alert": alert,
            "status": "active",
        }

    detected_alert: Optional[AlertEvent] = None
    highest_severity_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    best_severity = 0

    for metric_name, config in METRIC_QUERIES.items():
        profile = METRIC_PROFILES[metric_name]
        current_value = snapshot.get(metric_name)
        if current_value is None:
            _consecutive_counts[metric_name] = 0
            continue

        try:
            history = _query_range(
                config["query"],
                hours=profile.baseline_hours,
                step=profile.query_step,
            )
        except TypeError:
            history = _query_range(
                config["query"],
                hours=profile.baseline_hours,
            )
        if not history:
            history = _metric_history.get(metric_name, [])
        _metric_history[metric_name] = history[-288:] if history else []

        if len(history) < profile.minimum_samples:
            logger.debug(
                "Monitor: insufficient history for %s (%s samples)", metric_name, len(history)
            )
            continue

        mean = statistics.mean(history)
        std = statistics.stdev(history) if len(history) > 1 else 0.0

        if not _deviation_matches_direction(profile.threshold_direction, current_value, mean):
            _consecutive_counts[metric_name] = 0
            continue

        if not _passes_minimum_delta(metric_name, current_value, mean):
            _consecutive_counts[metric_name] = 0
            continue

        if std < profile.minimum_stddev:
            severity = _fallback_severity_for_flat_baseline(metric_name, current_value, mean)
            if severity == "LOW":
                _consecutive_counts[metric_name] = 0
                continue
            _consecutive_counts[metric_name] += 1
            logger.info(
                "Monitor: flat-baseline anomaly reading %s/%s for %s - value=%.4f mean=%.4f",
                _consecutive_counts[metric_name],
                CONSECUTIVE_REQUIRED,
                metric_name,
                current_value,
                mean,
            )
            if _consecutive_counts[metric_name] >= CONSECUTIVE_REQUIRED:
                sev_order = highest_severity_order.get(severity, 0)
                if sev_order > best_severity:
                    best_severity = sev_order
                    detected_alert = AlertEvent.create(
                        service=config["service"],
                        metric_name=metric_name,
                        current=current_value,
                        mean=mean,
                        std=max(std, profile.minimum_stddev),
                        threshold=ZSCORE_THRESHOLD,
                        severity=severity,
                    )
                    logger.warning(
                        "Monitor: ALERT FIRED - %s on %s severity=%s (flat baseline fallback)",
                        metric_name,
                        config["service"],
                        severity,
                    )
            continue

        z_score = abs(current_value - mean) / std
        if z_score >= ZSCORE_THRESHOLD:
            _consecutive_counts[metric_name] += 1
            logger.info(
                "Monitor: anomaly reading %s/%s for %s - z=%.2f value=%.4f",
                _consecutive_counts[metric_name],
                CONSECUTIVE_REQUIRED,
                metric_name,
                z_score,
                current_value,
            )
            if _consecutive_counts[metric_name] >= CONSECUTIVE_REQUIRED:
                severity = _classify_severity(z_score)
                sev_order = highest_severity_order.get(severity, 0)
                if sev_order > best_severity:
                    best_severity = sev_order
                    detected_alert = AlertEvent.create(
                        service=config["service"],
                        metric_name=metric_name,
                        current=current_value,
                        mean=mean,
                        std=std,
                        threshold=ZSCORE_THRESHOLD,
                        severity=severity,
                    )
                    logger.warning(
                        "Monitor: ALERT FIRED - %s on %s severity=%s z=%.2f",
                        metric_name,
                        config["service"],
                        severity,
                        z_score,
                    )
        else:
            _consecutive_counts[metric_name] = 0

    updates: dict = {"raw_metrics": snapshot}
    if detected_alert:
        updates["alert"] = detected_alert
        updates["status"] = "active"
        logger.info("Monitor Agent: AlertEvent fired -> incident_id=%s", state["incident_id"])
    else:
        logger.info("Monitor Agent: all metrics normal.")

    return {**state, **updates}
