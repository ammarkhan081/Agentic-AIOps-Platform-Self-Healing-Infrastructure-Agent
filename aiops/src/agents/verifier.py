"""
Verifier Agent - Agent 04: Validation
After a fix is applied, monitors Prometheus for 60 seconds.
Confirms recovery or triggers retry loop (max 3 retries).
After 3 failed retries: unconditional escalation to HITL.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from ..graph.state import AIOpsState, AlertEvent
from ..observability.catalog import METRIC_PROFILES

logger = logging.getLogger("verifier-agent")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
RECOVERY_WINDOW_S = int(os.getenv("VERIFIER_WINDOW_SECONDS", "60"))
POLL_INTERVAL_S = 5
MAX_RETRIES = int(os.getenv("MAX_RETRY_COUNT", "3"))


def _query_metric(query: str) -> float | None:
    try:
        resp = httpx.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5.0,
        )
        data = resp.json()
        if data.get("status") == "success":
            results = data["data"]["result"]
            if results:
                return float(results[0]["value"][1])
    except Exception as exc:
        logger.warning("Verifier: Prometheus query error: %s", exc)
    return None


def _metric_recovered(alert: AlertEvent, raw_metrics_before: dict) -> bool:
    """
    Check if the anomalous metric has returned to a normal range.
    """
    profile = METRIC_PROFILES.get(alert.metric_name)
    if not profile:
        return True

    current = _query_metric(profile.verifier_query)
    if current is None:
        return False

    if profile.threshold_direction == "low":
        recovery_threshold = alert.expected_mean - (
            profile.recovery_stddev_multiplier * alert.expected_std
        )
        recovered = current >= recovery_threshold
    else:
        recovery_threshold = alert.expected_mean + (
            profile.recovery_stddev_multiplier * alert.expected_std
        )
        recovered = current <= recovery_threshold
    logger.info(
        "Verifier: %s current=%.4f threshold=%.4f recovered=%s",
        alert.metric_name,
        current,
        recovery_threshold,
        recovered,
    )
    return recovered


def _post_fix_metric_snapshot() -> dict:
    """
    Snapshot the same monitored metrics (using PromQL expressions) post-fix.
    """
    snapshot: dict[str, float] = {}
    for metric_name, profile in METRIC_PROFILES.items():
        val = _query_metric(profile.query)
        if val is not None:
            snapshot[metric_name] = val
    return snapshot


def verifier_agent(state: AIOpsState) -> AIOpsState:
    """
    Verifier Agent node.
    Reads: alert, raw_metrics, retry_count, selected_fix, current_hypothesis_idx
    Writes: recovery_confirmed, recovery_metrics, retry_count, current_hypothesis_idx
    """
    alert = state.get("alert")
    retry_count = state.get("retry_count", 0)
    selected_fix = state.get("selected_fix")

    if not alert:
        logger.error("Verifier Agent: no alert in state")
        return {**state, "recovery_confirmed": False}

    logger.info(
        "Verifier Agent: monitoring recovery for %s - fix=%s window=%ss",
        alert.service,
        selected_fix.action_type if selected_fix else "unknown",
        RECOVERY_WINDOW_S,
    )

    polls = max(1, RECOVERY_WINDOW_S // POLL_INTERVAL_S)
    confirmed = False
    for idx in range(polls):
        time.sleep(POLL_INTERVAL_S)
        if _metric_recovered(alert, state.get("raw_metrics", {})):
            confirmed = True
            logger.info("Verifier Agent: recovery confirmed after %ss", (idx + 1) * POLL_INTERVAL_S)
            break

    recovery_metrics: dict = _post_fix_metric_snapshot()

    if not confirmed:
        new_retry = retry_count + 1
        next_hypothesis_idx = state.get("current_hypothesis_idx", 0) + 1
        logger.warning(
            "Verifier Agent: recovery NOT confirmed. retry_count=%s/%s next_hypothesis_idx=%s",
            new_retry,
            MAX_RETRIES,
            next_hypothesis_idx,
        )
        if new_retry >= MAX_RETRIES:
            logger.error("Verifier Agent: max retries reached - escalating to HITL")
        return {
            **state,
            "recovery_confirmed": False,
            "recovery_metrics": recovery_metrics,
            "retry_count": new_retry,
            "current_hypothesis_idx": next_hypothesis_idx,
        }

    logger.info("Verifier Agent: system recovered successfully")
    return {
        **state,
        "recovery_confirmed": True,
        "recovery_metrics": recovery_metrics,
        "retry_count": retry_count,
    }
