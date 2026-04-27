"""Shared observability metric catalog for ASHIA."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MetricProfile:
    name: str
    service: str
    query: str
    verifier_query: str
    threshold_direction: str
    description: str
    minimum_samples: int = 6
    minimum_stddev: float = 1e-10
    minimum_absolute_delta: float = 0.0
    minimum_relative_delta: float = 0.0
    recovery_stddev_multiplier: float = 1.0
    baseline_hours: int = 1
    query_step: str = "30s"


METRIC_PROFILES: dict[str, MetricProfile] = {
    "order_error_rate": MetricProfile(
        name="order_error_rate",
        service="order-service",
        query="sum(rate(order_errors_total[5m])) or vector(0)",
        verifier_query="sum(rate(order_errors_total[2m])) or vector(0)",
        threshold_direction="high",
        description="Order service error rate spike",
        minimum_absolute_delta=0.02,
        minimum_relative_delta=0.5,
    ),
    "order_request_latency_p95": MetricProfile(
        name="order_request_latency_p95",
        service="order-service",
        query=(
            "histogram_quantile(0.95, sum by (le) "
            "(rate(order_request_duration_seconds_bucket[5m])))"
        ),
        verifier_query=(
            "histogram_quantile(0.95, sum by (le) "
            "(rate(order_request_duration_seconds_bucket[2m])))"
        ),
        threshold_direction="high",
        description="Order service p95 latency degradation",
        minimum_absolute_delta=0.15,
        minimum_relative_delta=0.25,
    ),
    "order_memory_leak_bytes": MetricProfile(
        name="order_memory_leak_bytes",
        service="order-service",
        query="order_memory_leak_bytes",
        verifier_query="order_memory_leak_bytes",
        threshold_direction="high",
        description="Order service memory leak detected",
        minimum_absolute_delta=5 * 1024 * 1024,
        minimum_relative_delta=0.2,
    ),
    "order_queue_size": MetricProfile(
        name="order_queue_size",
        service="order-service",
        query="order_queue_size",
        verifier_query="order_queue_size",
        threshold_direction="high",
        description="Order service queue depth saturation",
        minimum_absolute_delta=5.0,
        minimum_relative_delta=0.25,
    ),
    "redis_cache_pressure_ratio": MetricProfile(
        name="redis_cache_pressure_ratio",
        service="order-service",
        query="redis_cache_pressure_ratio",
        verifier_query="redis_cache_pressure_ratio",
        threshold_direction="high",
        description="Redis cache memory pressure / overflow detected",
        minimum_absolute_delta=0.08,
        minimum_relative_delta=0.15,
    ),
    "user_error_rate": MetricProfile(
        name="user_error_rate",
        service="user-service",
        query="sum(rate(user_errors_total[5m])) or vector(0)",
        verifier_query="sum(rate(user_errors_total[2m])) or vector(0)",
        threshold_direction="high",
        description="User service error rate spike",
        minimum_absolute_delta=0.02,
        minimum_relative_delta=0.5,
    ),
    "user_request_latency_p95": MetricProfile(
        name="user_request_latency_p95",
        service="user-service",
        query=(
            "histogram_quantile(0.95, sum by (le) (rate(user_request_duration_seconds_bucket[5m])))"
        ),
        verifier_query=(
            "histogram_quantile(0.95, sum by (le) (rate(user_request_duration_seconds_bucket[2m])))"
        ),
        threshold_direction="high",
        description="User service p95 latency degradation",
        minimum_absolute_delta=0.1,
        minimum_relative_delta=0.25,
    ),
    "user_db_connections": MetricProfile(
        name="user_db_connections",
        service="user-service",
        query="user_db_connections_active",
        verifier_query="user_db_connections_active",
        threshold_direction="high",
        description="DB connection pool near exhaustion",
        minimum_absolute_delta=2.0,
        minimum_relative_delta=0.2,
    ),
    "user_request_rate": MetricProfile(
        name="user_request_rate",
        service="user-service",
        query="sum(rate(user_requests_total[5m])) or vector(0)",
        verifier_query="sum(rate(user_requests_total[2m])) or vector(0)",
        threshold_direction="high",
        description="User service request rate spike",
        minimum_absolute_delta=0.5,
        minimum_relative_delta=0.35,
    ),
    "gateway_error_rate": MetricProfile(
        name="gateway_error_rate",
        service="api-gateway",
        query="sum(rate(gateway_errors_total[5m])) or vector(0)",
        verifier_query="sum(rate(gateway_errors_total[2m])) or vector(0)",
        threshold_direction="high",
        description="API gateway upstream error rate spike",
        minimum_absolute_delta=0.02,
        minimum_relative_delta=0.5,
    ),
    "gateway_latency_p95": MetricProfile(
        name="gateway_latency_p95",
        service="api-gateway",
        query=(
            "histogram_quantile(0.95, sum by (le) "
            "(rate(gateway_request_duration_seconds_bucket[5m])))"
        ),
        verifier_query=(
            "histogram_quantile(0.95, sum by (le) "
            "(rate(gateway_request_duration_seconds_bucket[2m])))"
        ),
        threshold_direction="high",
        description="API gateway p95 latency degradation",
        minimum_absolute_delta=0.1,
        minimum_relative_delta=0.25,
    ),
    "gateway_request_rate": MetricProfile(
        name="gateway_request_rate",
        service="api-gateway",
        query="sum(rate(gateway_requests_total[5m])) or vector(0)",
        verifier_query="sum(rate(gateway_requests_total[2m])) or vector(0)",
        threshold_direction="high",
        description="API gateway request surge / traffic anomaly",
        minimum_absolute_delta=1.0,
        minimum_relative_delta=0.35,
    ),
}

METRIC_QUERIES = {
    name: {
        "query": profile.query,
        "service": profile.service,
        "threshold_direction": profile.threshold_direction,
        "description": profile.description,
    }
    for name, profile in METRIC_PROFILES.items()
}


def metric_profile_summary(name: str, profile: MetricProfile) -> dict[str, object]:
    """Serialize profile data for API responses."""
    return asdict(profile) | {"threshold_direction": profile.threshold_direction}
