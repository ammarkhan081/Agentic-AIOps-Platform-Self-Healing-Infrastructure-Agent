# CHANGELOG

## Unreleased

- Added shared engineering validation gate with backend and frontend checks
- Added backend API response schemas and explicit route response models
- Added GitHub Actions CI workflow for validation and backend tests
- Added pull request template, CODEOWNERS scaffold, contributor guide, branch protection guide, and release checklist

## v1.0.0 - March 2026

- 6-agent LangGraph pipeline with full state machine
- Monitor Agent Z-score detection on 12 Prometheus metrics
- Root Cause Agent with provider abstraction, strict schema validation, token budget enforcement, and Redis response caching
- Pinecone-backed continual learning memory with 10 synthetic bootstrap incidents
- Remediation Agent with concrete restart, cache flush, scale, config patch, reset, and rollback execution paths
- Verifier Agent with 60-second recovery window and ranked-hypothesis retry loop
- HITL Supervisor with Slack notifications, override execution, and timeout escalation
- Risk-tiered execution where LOW auto-runs and MEDIUM/HIGH requires human approval
- FastAPI REST, WebSocket incident streaming, and OpenAPI docs at `/api/v1/docs`
- React dashboard, fault lab, reports, memory browser, incident detail, and history views
- JWT auth with admin, sre, and viewer RBAC
- PostgreSQL persistence with audit trail and postmortem storage
- Full Docker Compose stack for local deployment
- Focused unit and integration test suite
