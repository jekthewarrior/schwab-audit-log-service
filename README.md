# Audit Log Service

Tamper-evident audit log service (Schwab AI-assisted engineering assignment).

## Stack

- Python 3.13, FastAPI, SQLAlchemy 2.0 (async) + Alembic, PostgreSQL, asyncpg
- `uv` for dependency/env management
- pytest, ruff, mypy, bandit, pip-audit for quality gates

## Documentation

| Doc | Contents |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components, data model, API surface, hash chain design, security model |
| [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) | Raw requirements, ambiguities, decisions, rationale, rejected alternatives |
| [`docs/TASKS.md`](docs/TASKS.md) | Implementation task breakdown, dependency-ordered, with live-verification notes |
| [`docs/TESTING.md`](docs/TESTING.md) | Testing approach, coverage, and what isn't automated |
| [`docs/ENGINEERING_SUMMARY.md`](docs/ENGINEERING_SUMMARY.md) | Plan, artifacts, risks found and fixed, trade-offs, assumptions, limitations |
| [`docs/AI_USAGE_LOG.md`](docs/AI_USAGE_LOG.md) | AI usage / traceability log |
| [`ATTESTATION.md`](ATTESTATION.md) | Submission attestation |

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

## Using the API

All endpoints are under `/audit`; examples assume the service is running at
`http://localhost:8000` (either option above). Full design rationale for each is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)'s API surface table and
[`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md).

**Write an event** — `POST /audit/events`

```bash
curl -X POST http://localhost:8000/audit/events \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "USER_LOGIN",
    "actorId": "user-1",
    "resourceType": "SESSION",
    "resourceId": "sess-1",
    "payload": {"ip": "127.0.0.1"},
    "timestamp": "2026-01-01T12:00:00Z"
  }'
```

Returns the stored record, including its `sequenceNumber`, `contentHash`, and
`prevHash` (64 zeros for the very first record — the genesis sentinel). All six
fields are required; `eventType` must match `^[A-Z][A-Z0-9_]*$` (free-form, not a
fixed enum — see 1a). No update or delete endpoint exists anywhere in this API.

**Query events** — `GET /audit/events`

```bash
curl "http://localhost:8000/audit/events?actorId=user-1&eventType=USER_LOGIN&limit=10"
```

Filters (`actorId`, `resourceType`, `resourceId`, `eventType`, `from`, `to`) combine
with AND; `resourceType`/`resourceId` are independently valid without each other.
`from`/`to` filter the caller-supplied `timestamp`. Results are cursor-paginated,
newest first — pass the response's `nextCursor` as `?cursor=<value>` for the next
page. Archived records are excluded by default; add `includeArchived=true` to
include them (though most of their filterable fields will already be `null` — see
[`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) Scenario B item 1e).

**Verify the chain** — `GET /audit/verify`

```bash
curl http://localhost:8000/audit/verify
```

`{"intact": true, ...}`, or on the first inconsistency found:
`{"intact": false, "sequenceNumber": N, "violationType": "CONTENT_MISMATCH" | "LINK_MISMATCH" | "GENESIS_MISMATCH", "detail": "..."}`.

**Redact a field** — `POST /audit/events/{sequenceNumber}/redact`

```bash
curl -X POST http://localhost:8000/audit/events/1/redact \
  -H "Content-Type: application/json" \
  -d '{"field": "accountNumber", "actorId": "compliance-officer-1"}'
```

Overwrites one top-level `payload` field with a redaction marker; `contentHash` is
unchanged, so the chain still verifies afterward. `404` if the record or field
doesn't exist, `409` if the field is already redacted or the record has been
archived (nothing left to redact).

**Archive old records** — `POST /audit/retention/sweep`

```bash
curl -X POST http://localhost:8000/audit/retention/sweep \
  -H "Content-Type: application/json" \
  -d '{"actorId": "cron-scheduler"}'
```

Archives records older than `RETENTION_WINDOW_DAYS` (default 365, based on
server-assigned `recordedAt`, not the caller-supplied `timestamp`). Returns the
`sequenceNumber`s archived by this call (empty if nothing was eligible — safe to
call repeatedly, e.g. from a cron job).

**Export a signed bundle** — `GET /audit/export`, and **fetch the verification
public key** — `GET /audit/export/public-key`. See
[Compliance reporting](#compliance-reporting-scenario-c) below for the full
walkthrough, including how a recipient verifies the bundle offline.

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
