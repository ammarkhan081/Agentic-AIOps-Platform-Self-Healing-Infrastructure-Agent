"""
Control-plane self-observability metrics.

These are exported for Prometheus scraping so ASHIA can monitor itself.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

incidents_detected_total = Counter(
    "incidents_detected_total",
    "Total incidents detected/triggered by the ASHIA control plane",
)

incidents_resolved_total = Counter(
    "incidents_resolved_total",
    "Total incidents resolved by the ASHIA control plane",
)

hitl_interventions_total = Counter(
    "hitl_interventions_total",
    "Total HITL intervention points triggered",
)

avg_time_to_recovery_seconds = Gauge(
    "avg_time_to_recovery_seconds",
    "Rolling average time to recovery for resolved incidents (seconds)",
)

_recovery_count = 0
_recovery_sum = 0.0


def observe_incident_detected() -> None:
    incidents_detected_total.inc()


def observe_hitl_intervention() -> None:
    hitl_interventions_total.inc()


def observe_incident_resolved(time_to_recovery_seconds: float | None) -> None:
    global _recovery_count, _recovery_sum
    incidents_resolved_total.inc()
    if time_to_recovery_seconds is None:
        return
    _recovery_count += 1
    _recovery_sum += float(time_to_recovery_seconds)
    avg_time_to_recovery_seconds.set(_recovery_sum / _recovery_count)


def prometheus_export() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def control_plane_summary() -> dict[str, float]:
    return {
        "incidents_detected_total": float(incidents_detected_total._value.get()),
        "incidents_resolved_total": float(incidents_resolved_total._value.get()),
        "hitl_interventions_total": float(hitl_interventions_total._value.get()),
        "avg_time_to_recovery_seconds": float(avg_time_to_recovery_seconds._value.get()),
    }
