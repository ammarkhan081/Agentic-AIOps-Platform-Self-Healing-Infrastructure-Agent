Alembic migration scaffolding for ASHIA database evolution.

Included files:
- `env.py` for online/offline Alembic execution
- `script.py.mako` revision template
- `versions/20260410_000001_initial_schema.py` baseline schema revision

The runtime still supports metadata bootstrap (`create_tables`) for local demos,
while this folder provides the formal migration surface required by the project
specification and production-style schema tracking.
