# AI Usage Log

Running log of AI-assisted decisions and work on this project, per the assignment's
traceability requirement (§4.3). Entries are added as work happens, not reconstructed
after the fact. Each entry: intent/prompt, what the AI produced, what was accepted /
modified / rejected, and why. Commits touching AI-assisted code carry a
`Co-Authored-By: Claude` trailer, which cross-references against these entries.

Status tags: **PROVISIONAL** (expected to be revisited), **FINAL** (settled for now).

---

## 2026-08-21 — Initial tech stack selection

**Intent:** Choose a Python + Postgres stack for the audit log service — web framework,
ORM/DB driver, hash algorithm, pagination strategy, and supporting tooling
(lint/type-check/security/test).

**AI produced:** Recommendation of FastAPI (Pydantic validation + auto OpenAPI docs),
SQLAlchemy 2.0 async + Alembic (kept separate from SQLModel to avoid coupling DB models
to API schemas, anticipating Scenario B's redaction needing divergent representations),
asyncpg driver, SHA-256 for the hash chain, keyset/cursor pagination over offset
pagination, and a tooling set: ruff, mypy, bandit, pip-audit, pytest/pytest-asyncio/httpx,
testcontainers (real Postgres in tests, not SQLite), uv, Docker + docker-compose.

**Decision:** Accepted the framework/ORM/driver/tooling choices as the working
baseline — **FINAL** for now, low cost to change later since they're infrastructure, not
business logic.

Accepted hash algorithm (SHA-256) and pagination strategy (keyset) as starting points
only — **PROVISIONAL**. Both are core to the hash-chain design and should be revisited
once Scenario A's requirements (chain semantics, verify-endpoint behavior, expected
result-set sizes) are fully decomposed, rather than locked in during a general stack
conversation.

**Rationale:** Don't want to bake tamper-evidence-critical design choices (hash
algorithm, chain walk/pagination interaction) into scaffolding before the requirements
that actually constrain them have been worked through. Infrastructure choices
(framework, driver, tooling) are cheap to keep; algorithmic choices tied to the core
guarantee are not.

---

## 2026-08-21 — Repo scaffolding

**Intent:** Stand up the project skeleton for the agreed stack so Scenario A
implementation has somewhere to land: `pyproject.toml` (uv-managed deps + ruff/mypy/
pytest/bandit config), FastAPI app skeleton with a `/health` endpoint, async
SQLAlchemy engine/session setup, Alembic scaffold (async-aware `env.py`), Dockerfile +
docker-compose (app + Postgres), `.gitignore`/`.dockerignore`, and this log.

**AI produced:** Full scaffold as described above; no audit-event models, hash-chain
logic, or API routes yet — those depend on Scenario A's task decomposition, not on the
stack setup itself.

**Decision:** Accepted as-is. Verified `docker compose config` and dependency install
(see below) before treating this as done.

**Rationale:** Keep infrastructure and business logic separable in the history — this
commit should contain no decisions about the hash chain or event schema, only the
runnable skeleton.

**Note:** `instructions.pdf` (the assignment brief) is gitignored rather than committed,
per §0.2's confidentiality instruction not to redistribute the assignment materials.

**Follow-up (same day):** initial full-container verification was blocked by a local
Docker permission issue (shell user not in the `docker` group). Deferred fixing this
myself since it meant changing the user's account/group membership — a system-level
change, not a project one. User granted `docker` group access; re-verified with
`docker compose up --build`: Postgres reports healthy, app container builds and starts,
`GET /health` returns `{"status": "ok"}`. Torn down after verification
(`docker compose down`) so no containers are left running. **FINAL** for the skeleton;
will need re-verification once real endpoints/migrations land.
