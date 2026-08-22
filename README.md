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

Integration tests spin up a real Postgres container via `testcontainers` (per the
testing-approach decision in `docs/REQUIREMENTS.md` — JSONB/numeric round-tripping
and role-based privilege behavior are Postgres-specific, not exercised by SQLite).
This needs direct Docker daemon access; if your shell doesn't have `docker` group
membership active, run `sg docker -c "uv run pytest"` instead. Test modules that
don't touch the database (`test_hashing.py`, `test_health.py`) never trigger the
container to start, so they stay fast regardless.

## Compliance reporting (Scenario C)

"Regulators need to be able to audit access to client account data" is satisfied by
the same signed export endpoint Scenario B built (`GET /audit/export`), not a
separate compliance-specific route — see `docs/REQUIREMENTS.md`'s Scenario C section
for the full clarification process. Internal compliance staff generate the report;
external regulators never authenticate to this system directly.

**Example:** produce a regulator-ready, independently-verifiable record of who
viewed a given account's data in a time window:

```bash
curl "http://localhost:8000/audit/export?resourceId=acct-123&eventType=ACCOUNT_VIEWED&from=2026-01-01T00:00:00Z&to=2026-06-30T23:59:59Z"
```

The returned bundle is self-contained and signed (Ed25519) — a recipient fetches the
current public key from `GET /audit/export/public-key` and can verify the bundle's
signature entirely offline, without any further access to this service. `eventType`
is caller-chosen, not enforced by the system, since 1a deliberately kept `eventType`
free-form rather than a fixed enum — "access" events are whatever convention your
producers actually use (e.g. `ACCOUNT_VIEWED`, `RECORD_ACCESSED`).

**Explicit scope boundaries** (from the Clarified Requirement Statement):
- No authentication/authorization on who may call this endpoint.
- No guarantee that every read-path in a broader system actually emits an access
  event — this service reports on what's captured, it doesn't instrument callers.
- No full-text/payload-content search — `resourceType`/`resourceId`/`actorId` are
  the only filters; client data referenced inside a payload of some other
  `resourceType` won't be found this way.
- Archived records (Scenario B retention) become unreachable through export's
  filters once their classification fields are nulled — see `docs/REQUIREMENTS.md`,
  Scenario B item 5e, for the known limitation and why it wasn't fixed.

## Quality gates

```bash
uv run ruff check .
uv run mypy .
uv run bandit -c pyproject.toml -r src
uv run pip-audit
```
