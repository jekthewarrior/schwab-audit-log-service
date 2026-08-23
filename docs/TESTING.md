# Testing Approach, Coverage, and Limitations

## Approach

Two layers, deliberately: extensive **live manual verification** against the real
running stack (Docker Compose) during development of every feature — documented
inline in [`TASKS.md`](TASKS.md) as each task was built — followed by a
**consolidated automated suite** (42 tests, `tests/`) that codifies those same
scenarios into repeatable `pytest` coverage.

Integration tests run against a **real PostgreSQL container** via `testcontainers`,
not SQLite. This was a deliberate decision, not a default: JSONB round-tripping,
numeric type fidelity, and — critically — the role-based privilege model (three
distinct Postgres roles with different grants) are Postgres-specific behaviors that
a lighter substitute wouldn't exercise. A test suite that mocked the database
couldn't have caught the crash bug described below, since it depends on how
Postgres actually returns JSONB data.

The fixture (`tests/conftest.py`) is lazy — a test module that never requests a DB
fixture (`test_hashing.py`, `test_health.py`) never triggers the container to
start, so pure-function tests stay fast. DB-backed tests get a fresh
`TRUNCATE audit_events` before each one, run against session-scoped engines bound
to the actual `app_role`/`maintenance_role`/admin roles migrations provision — not
mocked or stubbed permission checks.

**Environment note:** this needs direct Docker daemon access, distinct from
`docker compose`. Run `sg docker -c "uv run pytest"` if your shell doesn't have
`docker` group membership active in the current session.

## What testing found, not just what it covers

Two things surfaced specifically *because* tests ran against real infrastructure
rather than being written to already-known-correct behavior:

- **A crash bug in `verify_chain`.** `test_direct_content_tampering_is_detected`
  (`tests/test_acceptance.py`) tampers a record's `payload` via direct SQL by
  replacing it wholesale — a plausible, simple form of exactly the tampering req 9
  asks the system to catch. This crashed the verify endpoint with an unhandled
  `KeyError` instead of reporting `CONTENT_MISMATCH`, because the content-hash
  recomputation assumed every payload key has a matching commitment entry — true
  for every legitimate write, false the moment a key is added or renamed directly
  in the datastore. Fixed in `services/verify.py`, locked in as a named regression
  test. Manual `curl`-based verification during A4's development had only exercised
  *value* edits, never key addition, so it never hit this path.
- **A pytest-asyncio event-loop/connection-pool interaction.** The first full test
  run failed every DB-backed test with `"another operation is in progress"` — a
  session-scoped async engine combined with pytest-asyncio's default per-test event
  loop meant later tests tried to reuse asyncpg connections created under a
  different loop. Fixed via explicit session-scoped loop configuration in
  `pyproject.toml`.

Both are documented in full in `docs/AI_USAGE_LOG.md`'s entry for the consolidated
test suite.

## Coverage by area

| Area | File | Covers |
|---|---|---|
| Hashing primitives | `test_hashing.py` | Canonicalization determinism, salted-hash uniqueness (brute-force regression), content-hash sensitivity to every covered field, `recordHash` cascade sensitivity |
| Core acceptance flow | `test_acceptance.py` | req 9's full cycle — every violation type (`CONTENT_MISMATCH`, `LINK_MISMATCH` via tampering and deletion, both `GENESIS_MISMATCH` paths), empty-chain and clean-write intact cases |
| Concurrency | `test_concurrency.py` | 20 concurrent writers — no duplicate/gapped `sequence_number`s, resulting chain still verifies |
| Redaction | `test_redaction.py` | `content_hash` unchanged after redaction, verify stays intact, non-redacted fields in the same record remain tamper-detectable, retained hash is itself tamper-protected, all four error paths |
| Retention | `test_retention.py` | Selective archival by age, chain integrity preserved, gap detection unaffected, idempotency |
| Export | `test_export.py` | Independent third-party signature verification (via `cryptography` directly, not the app's own code), tamper detection on content+hash edited together, required-filter validation |
| Compliance scenario | `test_compliance.py` | `eventType` + time-range filter *combination* correctly isolates the intended subset (not just each filter individually), signature verification |
| DB privileges | `test_privileges.py` | `app_role` denied all mutation, `maintenance_role` denied on protected columns specifically, `maintenance_role` *can* update permitted columns (a positive control — without it, the negative tests could pass vacuously) |
| HTTP wiring | `test_health.py` | Liveness endpoint |

## What isn't automated, and why

- **Full HTTP-layer integration tests.** All DB-backed tests call service functions
  directly (`append_event`, `verify_chain`, etc.) against a real database, rather
  than going through FastAPI's HTTP layer with `httpx`. This was a scope trade-off:
  the HTTP layer (routing, request validation, dependency injection, response
  serialization) was extensively verified live via `curl` during each feature's
  development — documented per-task in `TASKS.md` — but that verification isn't
  captured as repeatable automated tests. A full HTTP-level suite would need
  `app.dependency_overrides` wired to the test container, which wasn't built in
  this pass.
- **Numeric JSONB round-trip edge case.** Flagged as a specific test to write back
  in [6c](REQUIREMENTS.md) (e.g. `100` vs `100.0` surviving storage without a false
  content-hash mismatch) — not yet added as its own test, though every integration
  test that writes and later verifies a record implicitly exercises the common
  case.
- **Load/scale testing.** Nothing exercises the system beyond the concurrency
  test's 20 writers or a handful of records per test. The fully-serialized-append
  design (7c) and O(n) verify walk (7e) are documented, accepted trade-offs for
  this prototype's scope, not validated against realistic production volume.
- **The DB-level defense-in-depth test only checks two representative columns**
  (`content_hash`, `sequence_number`) for `maintenance_role`'s denial, not every
  protected column individually — sufficient to prove the mechanism works, not
  exhaustive per-column coverage.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the design-level limitations these
tests validate against (no external chain anchoring, fully serialized appends, the
archived-record export gap), and [`ENGINEERING_SUMMARY.md`](ENGINEERING_SUMMARY.md)
for how these gaps weigh into overall risk.
