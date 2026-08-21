# Audit Log Service

Tamper-evident audit log service (Schwab AI-assisted engineering assignment).

## Stack

- Python 3.13, FastAPI, SQLAlchemy 2.0 (async) + Alembic, PostgreSQL, asyncpg
- `uv` for dependency/env management
- pytest, ruff, mypy, bandit, pip-audit for quality gates

Several stack details (hash algorithm, pagination strategy, and a few dependencies) are
provisional and expected to change once Scenario A's requirements are refined — see
[`docs/AI_USAGE_LOG.md`](docs/AI_USAGE_LOG.md) and the architecture doc (once written) for
current decisions and rationale.

## Local setup

### Option A: Docker Compose (app + Postgres)

```bash
docker compose up --build
```

App available at `http://localhost:8000`, health check at `http://localhost:8000/health`.

### Option B: Run locally against a Postgres container

```bash
docker compose up -d db
uv sync
uv run uvicorn audit_log_service.main:app --reload
```

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
