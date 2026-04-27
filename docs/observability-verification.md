# Observability Verification

This runbook is the calibration pass for ASHIA after infrastructure or target-service changes.

## Goals

- confirm Prometheus is scraping the right surfaces
- confirm Loki and Jaeger reflect the same fault story
- confirm ASHIA monitor thresholds line up with real target-system behavior

## Key control-plane endpoints

- `GET /api/v1/health`
- `GET /api/v1/metrics/summary`
- `GET /api/v1/metrics/observability-summary`
- `POST /api/v1/monitor/trigger`
- `GET /api/v1/demo/fault-status`
- `POST /api/v1/demo/prepare-scenario`

## Verification flow

1. Start the full stack with `docker compose up --build`.
2. Open Prometheus and verify scrapes for:
   - `api-gateway`
   - `user-service`
   - `order-service`
   - `aiops-control-plane`
3. Call `GET /api/v1/metrics/observability-summary`.
4. Check that each monitored metric reports:
   - a current value
   - a baseline mean/stddev when enough history exists
   - a `healthy`, `anomalous`, `anomalous_flat_baseline`, or `insufficient_history` status
5. Inject one fault at a time and verify:
   - Prometheus shows the metric movement
   - Loki shows the matching structured logs
   - Jaeger shows the matching trace slowdown or failure path
   - ASHIA monitor raises the expected incident family
6. Reset faults and confirm verifier metrics return to baseline range.

## Consecutive demo workflow

Between scenarios:

1. Call `POST /api/v1/demo/prepare-scenario`.
2. Let the cooldown complete.
3. Check `GET /api/v1/metrics/observability-summary`.
4. Confirm the next fault is not inheriting the prior scenario's alert shape.

Recommended demo defaults:

- `cooldown_seconds: 12`
- `warm_order_reads: 12`
- `warm_order_writes: 3`
- `warm_user_reads: 12`
- `reset_monitor: true`
- `clear_monitor_history: true`

## Calibration guidance

- If a metric flaps under low traffic, raise its minimum absolute or relative delta.
- If a fault is real but hidden by a flat baseline, keep the fallback path and tune the delta thresholds instead of reintroducing pure stddev-only logic.
- If the gateway reports `connection_error` when the real problem is upstream `500`, fix the taxonomy before tuning thresholds.
- Prefer threshold changes in the shared metric catalog so monitor, verifier, and operator views stay aligned.
