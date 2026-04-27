"""
Unit tests — Monitor Agent
Tests: Z-score detection, AlertEvent creation, severity classification
"""

from unittest.mock import patch

from src.agents.monitor import (
    METRIC_QUERIES,
    _classify_severity,
    _consecutive_counts,
    _deviation_matches_direction,
    monitor_agent,
)
from src.graph.state import AlertEvent, initial_state


class TestClassifySeverity:
    def test_critical_high_zscore(self):
        assert _classify_severity(6.0) == "CRITICAL"

    def test_high_zscore(self):
        assert _classify_severity(4.0) == "HIGH"

    def test_medium_zscore(self):
        assert _classify_severity(2.8) == "MEDIUM"

    def test_low_zscore(self):
        assert _classify_severity(1.0) == "LOW"

    def test_boundary_critical(self):
        assert _classify_severity(5.0) == "CRITICAL"

    def test_boundary_high(self):
        assert _classify_severity(3.5) == "HIGH"


class TestAlertEvent:
    def test_alert_creation(self):
        alert = AlertEvent.create(
            service="order-service",
            metric_name="order_memory_leak_bytes",
            current=5000.0,
            mean=100.0,
            std=50.0,
            threshold=2.5,
            severity="CRITICAL",
        )
        assert alert.service == "order-service"
        assert alert.metric_name == "order_memory_leak_bytes"
        assert alert.severity == "CRITICAL"
        assert alert.current_value == 5000.0
        assert alert.alert_id is not None
        assert alert.fired_at is not None
        assert "order-service" in alert.description

    def test_alert_z_score_in_description(self):
        alert = AlertEvent.create(
            service="user-service",
            metric_name="user_db_connections",
            current=95.0,
            mean=20.0,
            std=5.0,
            threshold=2.5,
            severity="HIGH",
        )
        assert "z-score" in alert.description.lower() or "σ" in alert.description


class TestMonitorAgent:
    def test_no_alert_when_metrics_normal(self):
        """Monitor should not fire alert when all metrics are within normal range."""
        state = initial_state()
        with (
            patch("src.agents.monitor._query_prometheus", return_value=0.01),
            patch("src.agents.monitor._query_range", return_value=[0.01] * 50),
        ):
            result = monitor_agent(state)
        assert result["alert"] is None or result.get("status") != "active"

    def test_alert_fires_after_consecutive_readings(self):
        """Alert should fire only after CONSECUTIVE_REQUIRED anomalous readings."""
        state = initial_state()
        # Reset counters
        for k in _consecutive_counts:
            _consecutive_counts[k] = 0

        normal_history = [0.01] * 100  # mean ~0.01, std ~0.0
        # To make std non-zero
        normal_history[-1] = 0.02

        with (
            patch("src.agents.monitor._query_prometheus", return_value=9999.0),
            patch("src.agents.monitor._query_range", return_value=normal_history),
        ):
            # First call — increments counters but doesn't fire
            result1 = monitor_agent(state)
            # Second call
            result2 = monitor_agent(state)
            # Third call — should now fire
            result3 = monitor_agent(state)

        # At least one of the results should have fired an alert
        fired = any(r.get("alert") is not None for r in [result1, result2, result3])
        assert fired, "Alert should have fired after 3 consecutive anomalous readings"

    def test_metrics_snapshot_always_populated(self):
        """raw_metrics should always be populated regardless of anomaly."""
        state = initial_state()
        with (
            patch("src.agents.monitor._query_prometheus", return_value=1.0),
            patch("src.agents.monitor._query_range", return_value=[1.0] * 20),
        ):
            result = monitor_agent(state)
        assert isinstance(result["raw_metrics"], dict)

    def test_handles_prometheus_unavailable(self):
        """Monitor should not crash when Prometheus is unreachable."""
        state = initial_state()
        with (
            patch("src.agents.monitor._query_prometheus", return_value=None),
            patch("src.agents.monitor._query_range", return_value=[]),
        ):
            result = monitor_agent(state)
        assert result is not None

    def test_high_direction_metric_ignores_downward_change(self):
        state = initial_state()
        for key in _consecutive_counts:
            _consecutive_counts[key] = 0

        history = [10.0 + (0.1 * idx) for idx in range(20)]
        with (
            patch("src.agents.monitor._query_prometheus", return_value=1.0),
            patch("src.agents.monitor._query_range", return_value=history),
        ):
            result = monitor_agent(state)

        assert result.get("alert") is None

    def test_flat_baseline_can_still_trigger_alert(self):
        state = initial_state()
        for key in _consecutive_counts:
            _consecutive_counts[key] = 0

        history = [0.0] * 20
        with (
            patch("src.agents.monitor._query_prometheus", return_value=20 * 1024 * 1024),
            patch("src.agents.monitor._query_range", return_value=history),
        ):
            result1 = monitor_agent(state)
            result2 = monitor_agent(state)
            result3 = monitor_agent(state)

        fired = any(r.get("alert") is not None for r in [result1, result2, result3])
        assert fired is True


class TestMetricQueries:
    def test_all_services_covered(self):
        services = {v["service"] for v in METRIC_QUERIES.values()}
        assert "order-service" in services
        assert "user-service" in services
        assert "api-gateway" in services

    def test_twelve_metrics_defined(self):
        assert len(METRIC_QUERIES) == 12

    def test_all_queries_have_required_fields(self):
        for name, config in METRIC_QUERIES.items():
            assert "query" in config, f"{name} missing 'query'"
            assert "service" in config, f"{name} missing 'service'"
            assert "description" in config, f"{name} missing 'description'"


def test_deviation_matches_direction():
    assert _deviation_matches_direction("high", 5.0, 2.0) is True
    assert _deviation_matches_direction("high", 1.0, 2.0) is False
    assert _deviation_matches_direction("low", 1.0, 2.0) is True
