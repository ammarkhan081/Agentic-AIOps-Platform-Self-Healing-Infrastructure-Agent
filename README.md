# ASHIA

ASHIA is a production-inspired agentic AIOps platform for self-healing infrastructure. It watches a live microservices system, detects anomalies from observability signals, reasons about root causes, proposes or executes remediation, validates recovery, and stores a structured postmortem so the next similar incident is handled faster.

This repository follows the ASHIA project specification while staying honest about its current runtime profile:

- `Groq` is the active default LLM provider during development for cost reasons.
- `OpenAI` remains wired as an optional migration path for heavier reasoning.
- `ChromaDB` is the active incident-memory implementation for local, reproducible demos.

## What ASHIA Does

ASHIA implements a 6-agent control loop:

1. `Monitor Agent` polls Prometheus and detects anomalies with a Z-score baseline.
2. `Root Cause Agent` correlates alert data with Loki logs, Jaeger traces, and incident memory.
3. `Remediation Agent` generates ranked fix options and auto-executes only low-risk actions.
4. `Verifier Agent` confirms recovery and advances to the next hypothesis if the fix failed.
5. `Learning Agent` writes a structured incident postmortem to memory.
6. `HITL Supervisor` pauses medium/high-risk actions for dashboard or Slack approval.

## Architecture

### Target system

- `api-gateway`
- `user-service`
- `order-service`
- OpenTelemetry tracing
- Prometheus metrics
- Loki structured logs
- Jaeger traces

### Control plane

- `FastAPI` backend
- `LangGraph` orchestration
- `PostgreSQL` for incidents, snapshots, and audit events
- `Redis` for operational caching and transient coordination
- `ChromaDB` + local sentence-transformer embeddings for incident memory
- `React + Vite + TypeScript` operator dashboard

Target-system realism notes:

- `user-service` now uses a real PostgreSQL connection pool for user reads and pool-exhaustion fault injection.
- `order-service` now uses Redis for order storage, short-lived list caching, queue depth, and cache-pressure fault simulation.
- all three target services now ship with container healthchecks and hardened Docker defaults.

### Current model strategy

- `Groq light/heavy` for current low-cost development
- `OpenAI gpt-4o / gpt-4o-mini` path kept ready in config

### Runtime profile

ASHIA is best described as a production-inspired systems prototype:

- multi-agent control loop over a live observability-backed demo stack
- realistic fault injection using actual Postgres and Redis behavior
- persisted incidents, audit events, and postmortems
- controlled auto-remediation only for low-risk lab actions

It is designed to demonstrate strong backend, observability, and systems-engineering judgment for portfolio, resume, and interview use.

## Key Features

- Automatic 30-second monitor loop with anomaly-triggered incident creation
- Closed-loop self-healing flow with retries and replanning
- Risk-tiered remediation with `LOW`, `MEDIUM`, and `HIGH` gates
- HITL approval, override, abort, and timeout escalation with Slack deep links into the dashboard
- Postmortem export in `markdown`, `json`, and `pdf`
- Continual learning memory with seeded synthetic incidents
- Seeded synthetic incidents for cold-start memory bootstrap
- Dashboard, history, reports, memory browser, and fault lab UI
- Role-aware controls for `admin`, `sre`, and `viewer`
- Swagger/OpenAPI docs at `http://localhost:8080/api/v1/docs`

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
```

Minimum variables to fill:

- `GROQ_API_KEY`
- `JWT_SECRET`

Recommended:

- `LANGCHAIN_API_KEY`
- `LANGCHAIN_PROJECT`
- `SLACK_WEBHOOK_URL`
- `FRONTEND_BASE_URL`

Optional:

- `OPENAI_API_KEY` if you want to switch providers
- `PINECONE_API_KEY` only if you decide to extend the legacy Pinecone adapter path; ChromaDB remains the default incident-memory backend

### 2. Start the full stack

```bash
docker compose up --build
```

The AIOps control plane starts its automatic monitor loop on boot. It polls on the configured
`MONITOR_POLL_INTERVAL_SECONDS` cadence and only creates incidents when the monitor detects an anomaly.

### 3. Open the main surfaces

- Dashboard: `http://localhost:3000`
- AIOps API docs: `http://localhost:8080/api/v1/docs`
- Readiness probe: `http://localhost:8080/readyz`
- Liveness probe: `http://localhost:8080/livez`
- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Jaeger: `http://localhost:16686`

Note: API routes require JWT authentication, including control-plane health and metrics routes.

Useful calibration endpoints after the stack is warm:

- `GET /api/v1/metrics/summary`
- `GET /api/v1/metrics/observability-summary`
- `POST /api/v1/monitor/trigger`
- `POST /api/v1/demo/prepare-scenario`

### 4. Use a seeded account

Demo users are seeded only when `SEED_DEMO_USERS=true` in `.env`.
The example env now defaults this to `false`, so demo credentials are opt-in rather than automatic.

- `admin / admin123`
- `ammar / ammar123`
- `viewer / viewer123`

## Fault Injection

ASHIA currently supports the spec's core six demo fault categories, plus realism extensions:

- `memory_leak`
- `cpu_spike`
- `db_exhaustion`
- `slow_query`
- `error_rate`
- `redis_overflow`
- `cascade_failure`
- `rollback` (high-risk demo extension)

Examples:

```bash
python scripts/inject_fault.py --list
python scripts/inject_fault.py --type memory_leak
python scripts/inject_fault.py --type db_exhaustion --service user-service
python scripts/inject_fault.py --type redis_overflow --ratio 0.95
python scripts/inject_fault.py --type rollback --target-version v0.9.0
python scripts/inject_fault.py --reset
```

The dashboard also exposes a compact fault console and a dedicated `Fault Lab` page.

For consecutive demos, use the scenario-prep endpoint between faults to reset target-service
fault state, optionally reset monitor memory/counters, and rewarm a small baseline of traffic:

```bash
curl -X POST http://localhost:8080/api/v1/demo/prepare-scenario \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "cooldown_seconds": 12,
    "warm_order_reads": 12,
    "warm_order_writes": 3,
    "warm_user_reads": 12,
    "reset_monitor": true,
    "clear_monitor_history": true
  }'
```

Real dependency behavior:

- `db_exhaustion` now reserves real PostgreSQL pool connections inside `user-service`
- `redis_overflow` now drives actual Redis memory pressure inside `order-service`
- order listing and creation now operate against Redis-backed state rather than pure in-memory simulation
- seeded users and seeded orders create a more realistic warm-start baseline for metrics, logs, and traces

## Memory Bootstrap

On AIOps API startup, ASHIA seeds Chroma-backed incident memory with `10` synthetic historical incidents spanning the core incident families. This avoids an empty cold-start memory and makes repeated-incident retrieval demos work immediately.

Seeded patterns include:

- memory leak
- slow query / latency regression
- error-rate spike
- Redis overflow
- CPU/request surge
- DB exhaustion
- gateway/upstream degradation

## Verification

### CI validation

GitHub Actions runs the shared engineering gate automatically on pushes and pull requests:

- workspace validation via `scripts/validate.ps1`
- backend test suite via `python -m pytest -q`

The workflow lives at:

- `.github/workflows/ci.yml`

Contributor and merge-process guidance lives in:

- `CONTRIBUTING.md`
- `docs/branch-protection.md`
- `docs/release-checklist.md`
- `docs/observability-verification.md`

### Targeted backend tests

```bash
cd aiops
python -m pytest -q tests/unit/test_health_routes.py tests/unit/test_reports_routes.py tests/unit/test_resilience_flows.py tests/unit/test_monitor.py tests/unit/test_graph_routing.py
```

### Full backend suite

```bash
cd aiops
python -m pytest -q
```

### Frontend build

```bash
cd frontend
npm run build
```

Status:

- verified successfully in an unrestricted Windows runtime
- restricted sandboxes may still fail with `vite/esbuild spawn EPERM`

### Coverage report

Coverage output is expected under `docs/coverage/index.html` after running:

```bash
cd aiops
python -m pytest --cov=src --cov-report=html:..\\docs\\coverage
```

Current committed coverage artifact:

- `docs/coverage/index.html`
- latest focused-suite coverage run: `83%`

## LangSmith

ASHIA is ready to publish LangSmith traces when these are configured:

- `LANGCHAIN_TRACING_V2=true`
- `LANGCHAIN_API_KEY`
- `LANGCHAIN_PROJECT=ashia-aiops`

Add your LangSmith project URL here after the first fully traced run set:

- `LangSmith project link: REPLACE_WITH_LANGSMITH_PROJECT_URL`

## Demo Video

Add your final demo video link here:

- `Demo video (Loom or equivalent): REPLACE_WITH_DEMO_VIDEO_URL`

## Deployment Notes

- `docker-compose.yml` runs the full end-to-end platform.
- `docker-compose.prod.yml` adds stricter restart and healthcheck behavior for production-style runs.
- `target-system/docker-compose.yml` can be used separately for the demo system.
- Bonus deployment target from the spec: `Railway` or `Render`
- Public deployment link: `REPLACE_WITH_PUBLIC_DEPLOYMENT_URL` (bonus deliverable)

## Security Notes

- The remediation layer may need Docker access for automated restart actions.
- Review any Docker socket exposure carefully before using this project beyond a controlled demo or lab environment.
- Do not ship real secrets in `.env` or commit history.
- Rotate all local credentials before any public release.

## Demo Scope and Current Limits

This repository is intentionally positioned as a production-inspired portfolio project, not a claim of enterprise production readiness.

- active incident execution state and HITL coordination are optimized for a single control-plane instance
- default demo accounts are useful for local evaluation but should be disabled for stricter environments
- automated remediation is limited to controlled lab actions such as restart, reset, rollback simulation, and config/fault endpoints
- the primary incident-memory implementation is local ChromaDB; a Pinecone migration path is optional and not the default runtime

That scope is deliberate: the goal is to show strong systems design, observability integration, and agent orchestration with realistic engineering tradeoffs.

## Repository Layout

- `aiops/` backend control plane
- `frontend/` React operator UI
- `target-system/` demo microservices + observability config
- `scripts/` utilities including fault injection
- `docs/coverage/` generated test coverage report target

Spec-surface implementation notes:

- `frontend/src/components/` now centers on shared operator UI primitives and page-specific composition.
- `frontend/src/hooks/` contains only actively used, focused hooks.
- `aiops/src/db/migrations/` now includes Alembic scaffolding (`env.py`, `script.py.mako`, and versioned revisions) to match the formal repository structure.
- `aiops/src/api/middleware/` exposes compatibility adapters for auth/rbac/audit concerns while route-level guards remain active.

## Deliverable Status

- Full Docker Compose stack: `present`
- Swagger/OpenAPI docs at `/api/v1/docs`: `present`
- Fault injector with documented usage: `present`
- Seeded memory bootstrap: `present`
- Frontend production build verification: `present`
- HTML coverage artifact in docs/coverage: `present`
- Coverage target 80%+: `met (latest focused suite: 83%)`
- LangSmith integration path: `present, link placeholder set in README`
- Demo video link: `placeholder set in README`
- Public deployment link: `bonus placeholder set in README`

Built for the ASHIA project specification, March 2026.
