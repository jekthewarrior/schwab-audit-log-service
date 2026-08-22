# Implementation Tasks

Concrete technical design and actionable task breakdown, derived from the decisions
in `docs/REQUIREMENTS.md`. Same per-scenario structure as that document; each task
notes which `REQUIREMENTS.md` decision(s) it implements, so a reviewer can trace any
piece of code back to the reasoning behind it. Tasks are written against the
**final** state of each design — where a later scenario amended an earlier decision
(e.g. Scenario B's 3a amending Scenario A's 6c), the task reflects the amended
version directly rather than building an interim version and revising it.

Ordered for dependency within each section; cross-scenario notes call out where an
earlier task already covers a later scenario's needs, to avoid building the same
thing twice.

---

## Scenario A — Core Audit Log Service

### A1. Data layer

- [x] **A1.1 — `audit_events` SQLAlchemy model.** Columns: `sequence_number`
  (`BIGINT`, app-computed, primary key — used directly as the uniqueness constraint
  rather than a separate surrogate id + `UNIQUE`, simpler and avoids a redundant
  index), `event_type`, `actor_id`, `resource_type`, `resource_id`, `payload`
  (`JSONB`), `payload_field_commitments` (`JSONB`: `{fieldName: {hash, salt}}`),
  `timestamp` (caller-supplied), `recorded_at` (server-assigned), `content_hash`
  (`CHAR(64)`, `NOT NULL` always), `prev_hash` (`CHAR(64)`, `NOT NULL` always),
  `archived` (`BOOLEAN`, default `false`), `archived_at` (nullable). All other
  columns nullable at the DB level (retention nulls them on archival per Scenario B
  2b) — "required at write time" is enforced by the write-path schema (A2.4), not
  the column definition. Built with Scenario B's redaction (per-field commitments)
  and archival (flag columns) needs included from the start.
  *Implements: 1a–1d, 3a/3b (Scenario A); 3a (Scenario B, payload commitment
  structure); 1d (Scenario B, archival columns).*
  → `src/audit_log_service/models/audit_event.py`
- [x] **A1.2 — Alembic migration.** Creates the table above, indexes on `actor_id`,
  (`resource_type`, `resource_id`), `event_type`, `timestamp` (query filter
  support).
  *Implements: 7c, 4a–4c.* Depends on: A1.1.
  → `alembic/versions/710d78dcb974_create_audit_events_table.py`
- [x] **A1.3 — DB roles/grants migration.** `app_role`: `SELECT`, `INSERT` only, no
  `UPDATE`/`DELETE`/DDL. `maintenance_role`: `SELECT`, `INSERT` (refinement made
  during implementation, not in the original task description — needed so
  redaction/retention can append their own `FIELD_REDACTED`/`RECORD_ARCHIVED`
  system events in the *same transaction* as the column update, for atomicity,
  without switching roles mid-transaction), and column-level `UPDATE` on
  `payload`, `payload_field_commitments`, `event_type`, `actor_id`,
  `resource_type`, `resource_id`, `timestamp`, `recorded_at`, `archived`,
  `archived_at` — **never** on `sequence_number`, `content_hash`, `prev_hash`.
  Also required splitting `Settings.database_url` into three connection strings
  (`database_url` for `app_role`, `maintenance_database_url` for
  `maintenance_role`, `admin_database_url` for migrations only) — the original
  design didn't specify this, but it's what makes the least-privilege grants real
  rather than documented-but-unenforced (previously *all* connections, including
  the running app, used the Postgres superuser-equivalent account).
  *Implements: 2a (Scenario A); DB privilege (Scenario B, Group 3).* Depends on:
  A1.1.
  → `alembic/versions/adde66daf218_create_app_role_and_maintenance_role.py`,
  `src/audit_log_service/core/config.py`. Verified with a negative test: both
  `app_role` and `maintenance_role` get `permission denied` attempting to `UPDATE
  content_hash` directly via `psql`.

### A2. Write path

- [x] **A2.1 — Canonical serialization + hashing module.** Per-field salted
  commitment for `payload` (`fieldHash(key) = SHA256(salt || canonical(key) ||
  canonical(value))`, fresh random salt per field; `payloadCommitment =
  SHA256(canonical(sorted (fieldName, fieldHash) pairs))`); combined `content_hash`
  over the seven non-payload fields plus `payloadCommitment`.
  Implementation note: `field_hash` hashes `[salt, key, value]` as one canonical
  JSON array rather than concatenating three separate canonicalized strings — avoids
  ambiguity from variable-length-string concatenation, functionally equivalent to
  the documented design. `record_hash` (A2.2) safely uses plain string
  concatenation instead, since both its inputs are fixed-length 64-char hex strings
  with no such ambiguity.
  *Implements: 6a–6c (as amended by Scenario B 3a).*
  → `src/audit_log_service/core/hashing.py`; unit tests in `tests/test_hashing.py`
  (canonicalization determinism, salting produces different hashes for identical
  low-entropy values, content_hash sensitivity to every covered field, recordHash
  cascade sensitivity to prev_hash).
- [x] **A2.2 — `recordHash` derivation utility.** Transient, not stored:
  `SHA256(content_hash || prev_hash)`. Used by both the append service (A2.3) and
  verify (A4.1, not yet built).
  *Implements: 6d.* Depends on: A2.1.
  → `record_hash()` in `src/audit_log_service/core/hashing.py`.
- [x] **A2.3 — Append service.** Advisory-lock-scoped critical section
  (`pg_advisory_xact_lock` on a fixed constant key): read tail →
  `sequence_number = tail_seq + 1` → `prev_hash = recordHash(tail)` (or genesis
  `"0"*64` if no tail exists) → compute `content_hash` → insert → commit.
  *Implements: 7c, 7d, 7b.* Depends on: A2.1, A2.2, A1.2.
  → `src/audit_log_service/services/append.py`. Verified live: wrote two events via
  the running API, independently recomputed record 1's `recordHash` and confirmed it
  matches record 2's stored `prev_hash` exactly.
- [x] **A2.4 — Pydantic request/response schemas.** `eventType` pattern
  `^[A-Z][A-Z0-9_]*$`; all six fields required; `payload` constrained to a JSON
  object with size (~32KB) and nesting-depth (~10) caps. camelCase API surface
  (`eventType`, `actorId`, ...) over snake_case internals, via Pydantic's
  `alias_generator=to_camel` — not specified in the original task, added since the
  document's own examples use camelCase field names.
  *Implements: 1a–1d.*
  → `src/audit_log_service/schemas/event.py`.
- [x] **A2.5 — `POST /audit/events` endpoint.** Wires A2.4 validation → A2.3 append
  service. No update/delete routes exist anywhere in the API.
  *Implements: req 1, req 2.* Depends on: A2.3, A2.4.
  → `src/audit_log_service/api/events.py`. Added `SessionDep` (an `Annotated`
  session-dependency alias in `core/db.py`) as a small refactor over the original
  design so every future endpoint reuses one definition instead of repeating
  `Depends(get_session)`. Verified live: valid write returns 201 with correct chain
  fields; malformed `eventType` (lowercase) is rejected with 422 and consumes no
  `sequence_number` (confirmed via direct DB query) — validates 1d.

### A3. Query path

- [ ] **A3.1 — Cursor pagination utility.** Keyset pagination anchored to
  `sequence_number`, default sort descending.
  *Implements: 5a/5d, 5b.*
- [ ] **A3.2 — `GET /audit/events` endpoint.** Filters: `actorId`, `resourceType`
  (independently valid without `resourceId`), `resourceId`, `eventType`, `from`/`to`
  (against caller-supplied `timestamp`) — AND semantics across filters. Default
  `limit=50`/max `limit=500`. Excludes archived records unless `includeArchived=true`.
  *Implements: 4a–4c, 5c (Scenario A); 1e (Scenario B).* Depends on: A3.1, A1.2.

### A4. Verify path

- [ ] **A4.1 — Verify walk logic.** Per record, in `sequence_number` order: genesis
  check (`sequence_number=1`'s `prev_hash == "0"*64`); content check — **skip and
  trust the stored `content_hash` if `archived=true`** (Scenario B 2b), otherwise
  recompute using retained per-field hashes for redacted fields (Scenario B 3a/3f)
  and fresh hashes for present fields; link check — compare `prev_hash` against
  `recordHash` of the record with the next-lower existing `sequence_number`, with
  gap sub-classification (missing `sequence_number`(s) named) when that gap is >1.
  Fail-fast: stop and report at the first inconsistency found.
  *Implements: 8a, 6d, 8b (Scenario A); 2a/2b/2c (Scenario B).* Depends on: A2.2,
  A1.2.
- [ ] **A4.2 — `GET /audit/verify` endpoint.** Single synchronous request, DB-streamed
  cursor (no full-table buffering in memory).
  *Implements: 7e.* Depends on: A4.1.

### A5. Tests

- [x] **A5.1 (partial) — Unit: canonical serialization determinism**, salting
  produces different hashes for identical low-entropy values, `content_hash`
  sensitivity to every covered field — done early, alongside A2.1, in
  `tests/test_hashing.py`. **Still open:** the numeric int/float JSONB round-trip
  case flagged in 6c (e.g. `100` vs `100.0`) — needs an actual DB round-trip, not a
  pure unit test of the hashing function in isolation; deferred to a DB-backed
  integration test once A3/A4 exist to query the round-tripped value back out.
- [ ] **A5.2 (partial) — Unit: `recordHash` cascade.** The hash-function-level
  property (`record_hash` output changes if `prev_hash` changes) is covered in
  `tests/test_hashing.py`, alongside A2.1, and manually verified live against two
  real records written through the running API (independently recomputed record 1's
  `recordHash`, matched record 2's stored `prev_hash` exactly). **Still open:** an
  automated, multi-record integration test that tampering with any single stored
  record invalidates every subsequent record's link — needs A4's verify logic to
  assert against, so deferred there.
- [ ] **A5.3 — Integration: req 9's full acceptance flow.** Write events → query →
  verify (intact) → direct DB mutation (bypassing the API) → verify again (catches
  it, reports the correct violation type per 8a).
- [ ] **A5.4 — Integration: concurrent writers.** Confirms the advisory lock (A2.3)
  serializes appends correctly — no duplicate or gapped `sequence_number`s under
  concurrent load.

---

## Scenario B — Retention & Redaction

Schema and DB-role tasks (A1.1, A1.3) already include what this scenario needs —
noted rather than duplicated. Verify's archived/redacted-record handling is already
folded into A4.1.

### B1. Redaction

- [ ] **B1.1 — `POST /audit/events/{sequence_number}/redact` endpoint.** Body names
  a top-level `payload` field path. Validates the field exists and isn't already
  redacted; overwrites its raw value with the structured marker
  (`{"__redacted__": true, "redactedAt", "redactionEventSeq"}`); retains its
  `(hash, salt)` in `payload_field_commitments` unchanged; runs through the
  `maintenance_role` connection (A1.3), never `app_role`.
  *Implements: 3a, 3b, 3c, 3d, 3e.* Depends on: A1.1, A1.3, A2.1.
- [ ] **B1.2 — Append `FIELD_REDACTED` system event.** Same append path as A2.3
  (via `app_role` — this is a normal chain-appended event, not a mutation),
  documenting which record/field/actor/timestamp. Runs inside the same transaction
  as B1.1's redaction write.
  *Implements: 3b (audit trail for the redaction action).* Depends on: A2.3, B1.1.

### B2. Retention

- [ ] **B2.1 — `POST /audit/retention/sweep` endpoint.** Finds records older than
  the configured `RETENTION_WINDOW_DAYS` (global setting) not already archived;
  nulls detail fields (`event_type`, `actor_id`, `resource_type`, `resource_id`,
  `payload`, `payload_field_commitments`, `timestamp`, `recorded_at`); sets
  `archived=true`/`archived_at`; runs through `maintenance_role`.
  *Implements: 1a, 1d, 1b, 1c (mechanism).* Depends on: A1.1, A1.3.
- [ ] **B2.2 — Append `RECORD_ARCHIVED` system event per sweep.** Mirrors B1.2's
  pattern for redaction — documents which `sequence_number`(s) were archived and
  when.
  *Implements: 1c (audit trail for the archival action).* Depends on: A2.3, B2.1.

### B3. Bulk export

- [ ] **B3.1 — Ed25519 signing key setup.** Generate keypair; private key loaded
  from an environment variable/secrets manager at startup, never committed; public
  key documented/exposed for recipients. `signingKeyId` supports future rotation.
  *Implements: 5c (operational note).*
- [ ] **B3.2 — `GET /audit/export` endpoint.** Requires at least one of
  `resourceId`/`actorId`; accepts optional `eventType` and `from`/`to` (composable
  with the required filter) — this same parameter set satisfies both Scenario B's
  original bulk-export ask and Scenario C's compliance-reporting extension, so no
  separate endpoint is built for C (see Scenario C section). Bundle: `exportedAt`,
  `filter`, `chainTailSnapshot` (`sequenceNumber` + `recordHash` of the chain tail
  at export time), `records` (sorted by `sequence_number`, each including its own
  `content_hash`/`prev_hash`/`sequence_number`, redaction markers and archived
  flags represented as-is per B1/B2 — no special-casing needed), `signingKeyId`,
  `signature` (Ed25519 over the canonical serialization of everything else).
  *Implements: 5a, 5b, 5d, 5e (Scenario B); the filter extension also implements
  Scenario C's technical design.* Depends on: A2.1, A3.2 (filter logic reuse),
  B3.1.

### B4. Tests

- [ ] **B4.1 — Unit: redaction preserves chain validity.** Redact a field, confirm
  `content_hash` unchanged, confirm verify (A4.1) still reports intact, confirm the
  *other* fields in the same payload are still independently tamper-detectable
  (regression test for the whole-payload-hash failure mode identified while
  resolving 3a).
  *Implements: 3a, 3f.*
- [ ] **B4.2 — Unit: archival preserves chain validity, no false positive.**
  Archive a record, confirm verify reports intact (2b's new branch exercised),
  confirm gap detection is unaffected (2c).
  *Implements: 1d, 2a, 2b, 2c.*
- [ ] **B4.3 — Unit: redacted field salting.** Confirm two records with the same
  redacted value produce *different* stored hashes (distinct salts) — salting
  defeats brute-force even for low-entropy values like a 9-digit account number —
  regression test for the brute-force risk identified in 3a.
- [ ] **B4.4 — Integration: export bundle signature.** Tamper with an exported
  bundle's JSON (any field, including a record's own `content_hash`), confirm
  signature verification fails — the scenario the rechaining-vs-signature analysis
  (5a) was specifically evaluating.
- [ ] **B4.5 — Integration: DB-level immutability.** Confirm `app_role`'s connection
  gets a permission error on any attempted `UPDATE`/`DELETE` against `audit_events`
  — proves 2a's defense-in-depth layer is real, not just documented intent.

---

## Scenario C — Compliance Reporting

No new endpoint or schema — the entire technical design is captured in B3.2's
`eventType`/`from`/`to` filter extension. What's left is validation and
documentation specific to the compliance use case.

- [ ] **C1 — Documentation: compliance reporting usage guidance.** README/
  architecture-doc section walking through the compliance use case end-to-end:
  e.g. "filter by `resourceId=<account>` + `eventType=ACCOUNT_VIEWED` + `from`/`to`
  to produce a regulator-ready, independently-verifiable access report," including
  the explicit scope boundaries from the clarified requirement (no auth/authz on
  who can call this, no guarantee every read-path in a broader system emits an
  access event, no payload-content search).
  *Implements: the Clarified Requirement Statement, Scenario C.*
- [ ] **C2 — Integration test: compliance report scenario.** Write a mix of
  `ACCOUNT_VIEWED` and other event types for the same `resourceId`; export filtered
  by `resourceId` + `eventType=ACCOUNT_VIEWED` + a time range that excludes some of
  the written events; confirm only the matching subset appears in the bundle and
  the signature verifies.
  *Implements: C1–C6, the Clarified Requirement Statement.* Depends on: B3.2.

**Explicitly out of scope (from the Clarified Requirement Statement, not omissions):**
authentication/authorization for who may run reports (C6); instrumentation
guaranteeing every application read-path emits an access event (not this service's
responsibility); full-text/payload-content search for account data referenced
inside non-account-`resourceType` records (C3 — partially mitigated by independent
`actorId`/time-range filtering, not solved); compliance with any specific named
regulation (C5); a human-formatted report document (PDF/CSV) — the deliverable is
the verifiable JSON bundle itself.

---

## Suggested build order

1. **A1 → A2 → A3 → A4 → A5** (Scenario A end-to-end, including its own tests,
   before layering B on top — B's mechanisms assume A's chain/hash/verify machinery
   already works and is tested).
2. **B1 and B2 can proceed in parallel** (redaction and retention don't depend on
   each other, only on A1/A2/A4). **B3 depends on both being done** if export needs
   to correctly represent redacted/archived records (B3.2 references B1/B2's field
   representations).
3. **B4** after B1–B3.
4. **C1/C2** last — pure documentation and a validation test against B3.2, no new
   implementation.
