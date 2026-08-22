# Audit Log Service

Tamper-evident audit log service (Schwab AI-assisted engineering assignment).

## Stack

- Python 3.13, FastAPI, SQLAlchemy 2.0 (async) + Alembic, PostgreSQL, asyncpg
- `uv` for dependency/env management
- pytest, ruff, mypy, bandit, pip-audit for quality gates

Design decisions (hash algorithm, chain structure, pagination strategy, etc.), with
rationale and rejected alternatives, are in [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md).
The resulting implementation task breakdown is in [`docs/TASKS.md`](docs/TASKS.md).
AI usage/traceability notes are in [`docs/AI_USAGE_LOG.md`](docs/AI_USAGE_LOG.md).

## Local setup

### Option A: Docker Compose (app + Postgres)

```bash
docker compose up --build
```

Runs Alembic migrations (which also provision `app_role`/`maintenance_role`, see
below) before starting the app. Available at `http://localhost:8000`, health check
at `http://localhost:8000/health`.

### Option B: Run locally against a Postgres container

```bash
docker compose up -d db
uv sync
uv run alembic upgrade head
uv run uvicorn audit_log_service.main:app --reload
```

### DB roles

Per [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) (2a, and Scenario B's "DB
privilege" decision), the running application never connects as the Postgres
superuser-equivalent account. Migrations provision two least-privilege roles:
`app_role` (`SELECT`, `INSERT` only) and `maintenance_role` (`SELECT`, `INSERT`, and
`UPDATE` scoped to specific columns — never `sequence_number`, `content_hash`, or
`prev_hash`). The superuser-equivalent account (`admin_database_url`) is used only by
Alembic, never by application runtime code. Dev-only default passwords live in
[`src/audit_log_service/core/config.py`](src/audit_log_service/core/config.py); see
that file's docstring for the production caveat.

## Tests

```bash
uv run pytest
```

## Quality gates

```bash
uv run ruff check .
uv run mypy .
uv run bandit -c pyproject.toml -r src
uv run pip-audit
```
