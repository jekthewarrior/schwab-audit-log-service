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

- [x] **A3.1 — Cursor pagination utility.** Keyset pagination anchored to
  `sequence_number`, default sort descending. Implemented as "fetch `limit + 1`
  rows, if the extra one exists set `nextCursor` to the last returned row's
  `sequence_number`" — avoids a separate `COUNT` query to determine if there's a
  next page.
  *Implements: 5a/5d, 5b.*
  → `list_events()` in `src/audit_log_service/services/query.py`.
- [x] **A3.2 — `GET /audit/events` endpoint.** Filters: `actorId`, `resourceType`
  (independently valid without `resourceId`), `resourceId`, `eventType`, `from`/`to`
  (against caller-supplied `timestamp`) — AND semantics across filters. Default
  `limit=50`/max `limit=500`. Excludes archived records unless `includeArchived=true`.
  Noted a non-obvious interaction in `list_events()`'s docstring: since archival
  (Scenario B) nulls every detail column, `includeArchived=true` combined with any
  other filter (actorId, resourceType, etc.) will never surface an archived record —
  a NULL never satisfies an equality/range comparison in SQL. This is consistent
  with 1e's documented limitation (archived content isn't meaningfully
  searchable), not a bug, but worth being explicit about for a future reader.
  *Implements: 4a–4c, 5c (Scenario A); 1e (Scenario B).* Depends on: A3.1, A1.2.
  → `src/audit_log_service/api/events.py`, `src/audit_log_service/schemas/query.py`.
  Verified live against the real stack: seeded 7 records across multiple
  actors/resourceTypes/eventTypes/timestamps; confirmed AND semantics
  (`actorId`+`eventType` narrows correctly), `resourceType`-alone filtering, `from`
  time-range filtering against caller timestamp, 3-page cursor pagination with no
  gaps or duplicates across all 7 records, and default-exclude/opt-in-include of a
  manually-archived record. Automated pytest coverage (needs a real Postgres
  fixture, not SQLite, per the testing-approach decision) deferred to A5, consistent
  with the original build order — not skipped, just sequenced after A4 as planned.

### A4. Verify path

- [x] **A4.1 — Verify walk logic.** Per record, in `sequence_number` order: genesis
  check (`sequence_number=1`'s `prev_hash == "0"*64`); content check — **skip and
  trust the stored `content_hash` if `archived=true`** (Scenario B 2b), otherwise
  recompute using retained per-field hashes for redacted fields (Scenario B 3a/3f)
  and fresh hashes for present fields; link check — compare `prev_hash` against
  `recordHash` of the record with the next-lower existing `sequence_number`, with
  gap sub-classification (missing `sequence_number`(s) named) when that gap is >1.
  Fail-fast: stop and report at the first inconsistency found.
  *Implements: 8a, 6d, 8b (Scenario A); 2a/2b/2c (Scenario B).* Depends on: A2.2,
  A1.2.
  → `src/audit_log_service/services/verify.py`, `src/audit_log_service/schemas/verify.py`.
  Required a small refactor of `hashing.py`, done alongside this task: split
  `payload_commitment` into a lower-level `payload_commitment_from_hashes(dict[str,
  str])` and `compute_content_hash` now takes an already-computed
  `payload_commitment_value` rather than the raw write-time commitments dict — lets
  the append service (all-fresh hashes) and verify (mixed fresh/retained-for-
  redacted hashes) share the same content-hash-building function while differing
  only in how they arrive at the commitment value. Also narrowed
  `payload_field_commitments`'s model type from `dict[str, object]` to
  `dict[str, dict[str, str]]` (A1.1) — its shape is fixed and self-controlled, unlike
  `payload` itself.
- [x] **A4.2 — `GET /audit/verify` endpoint.** Single synchronous request, DB-streamed
  cursor (no full-table buffering in memory), via `session.stream_scalars`.
  *Implements: 7e.* Depends on: A4.1.
  → `src/audit_log_service/api/verify.py`.

  **Verified live — this is req 9's full acceptance test, run for real against the
  running stack, not simulated:** empty chain → `intact: true`. Wrote 4 clean
  events → `intact: true`. Tampered a record's `payload` directly via `psql` →
  `CONTENT_MISMATCH` at the correct `sequence_number`; reverted, confirmed back to
  intact. Tampered a record's `prev_hash` directly → `LINK_MISMATCH` naming the
  correct predecessor; reverted (using the hashing module itself to compute the
  correct value) and confirmed intact again. Deleted an interior record outright →
  `LINK_MISMATCH` with `"Missing record(s) with sequence_number 3"` — the literal
  req 9 scenario (write → verify → tamper via direct datastore mutation → verify
  catches it). Tampered record 1's `prev_hash` → `GENESIS_MISMATCH`; deleted record
  1 outright → `GENESIS_MISMATCH` with `"chain begins at sequence_number=2"` — the
  two distinct genesis-violation paths identified while resolving 8a. Manually
  simulated an archived record (nulled detail columns, per Scenario B 2a/2b) →
  confirmed no false positive. Manually simulated a redacted field (marker +
  retained commitment, per Scenario B 3a/3e) → confirmed no false positive, **and**
  confirmed tampering with a *different, non-redacted* field in the same
  partially-redacted record still produces `CONTENT_MISMATCH` — this is the exact
  property that motivated rejecting a whole-payload hash during Scenario B's 3a
  discussion, now empirically confirmed in the running system rather than just
  reasoned about on paper.

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
- [x] **A5.3 — Integration: req 9's full acceptance flow, automated.** Write events
  → query → verify (intact) → direct DB mutation (bypassing the API, via the admin
  connection) → verify again (catches it, reports the correct violation type per
  8a). Covers all three violation types and both genesis-violation paths.
  → `tests/test_acceptance.py`, `tests/conftest.py` (the `testcontainers`-based
  Postgres fixture, built as part of this task — session-scoped container +
  migrations, function-scoped truncation between tests, separate `app`/
  `maintenance`/`admin` session fixtures matching the three DB roles).
  **Found and fixed a real bug, not just a test gap:** the direct-content-tampering
  test (wholesale payload replacement — a plausible, simple attack) crashed
  `verify_chain` with an unhandled `KeyError` instead of reporting
  `CONTENT_MISMATCH`, because `_recompute_content_hash` assumed every payload key
  has a matching `payload_field_commitments` entry — true for legitimate writes,
  false for a field an attacker added or renamed directly in the datastore. Fixed
  in `services/verify.py` (falls back to an empty salt for a missing commitment,
  which can never coincidentally match the real one, so it reliably produces
  `CONTENT_MISMATCH` instead of crashing) and locked in as a named regression test.
  This is exactly the value of testing against real Postgres with real tampering
  rather than only reasoning about the design on paper.
- [x] **A5.4 — Integration: concurrent writers.** Confirms the advisory lock (A2.3)
  serializes appends correctly — no duplicate or gapped `sequence_number`s under
  concurrent load, and the resulting chain still verifies intact.
  → `tests/test_concurrency.py` — 20 concurrent `append_event` calls via
  `asyncio.gather`, each on its own session from the same engine.

---

## Scenario B — Retention & Redaction

Schema and DB-role tasks (A1.1, A1.3) already include what this scenario needs —
noted rather than duplicated. Verify's archived/redacted-record handling is already
folded into A4.1.

### B1. Redaction

- [x] **B1.1 — `POST /audit/events/{sequence_number}/redact` endpoint.** Body names
  a top-level `payload` field path. Validates the record exists, isn't archived
  (nothing left to redact — 409), the field exists (404), and isn't already
  redacted (409); overwrites its raw value with the structured marker
  (`{"__redacted__": true, "redactedAt", "redactionEventSeq"}`); retains its
  `(hash, salt)` in `payload_field_commitments` unchanged; runs through the
  `maintenance_role` connection (A1.3), never `app_role`.
  *Implements: 3a, 3b, 3c, 3d, 3e.* Depends on: A1.1, A1.3, A2.1.
  → `src/audit_log_service/services/redact.py`, `src/audit_log_service/api/redact.py`,
  `src/audit_log_service/schemas/redact.py`. Extracted `is_redaction_marker` into a
  new `core/redaction.py` — shared between this file and `services/verify.py`,
  rather than duplicated, so the marker-detection logic can't drift out of sync
  between the writer and the reader of that structure.
- [x] **B1.2 — Append `FIELD_REDACTED` system event.** Runs via `maintenance_role`,
  not `app_role` as originally described — see A1.3's note: `maintenance_role`
  needed `INSERT` added specifically so this event and B1.1's `UPDATE` can commit
  atomically in one transaction, which isn't possible across two different
  roles/connections. Documents which record/field/actor/timestamp.
  *Implements: 3b (audit trail for the redaction action).* Depends on: A2.3, B1.1.

  **Verified live against the real stack:** wrote a record with a sensitive field,
  redacted it via the actual endpoint, confirmed the record's `content_hash` is
  **byte-for-byte identical** before and after redaction (the core proof that
  redaction never touches the hash chain), confirmed `/audit/verify` still reports
  intact, confirmed the `FIELD_REDACTED` event is discoverable via
  `GET /audit/events?eventType=FIELD_REDACTED` (3f's discoverability answer). Tested
  all four error paths (record not found, field not found, already redacted, and —
  via direct `psql` as `app_role` — confirmed `app_role` genuinely cannot perform
  the `UPDATE` at all, the role split is real). Tampered the *retained* hash for the
  redacted field directly via `psql` and confirmed `CONTENT_MISMATCH` still fires —
  empirically confirms the retained hash is itself transitively tamper-protected via
  `content_hash`, not a special trusted value exempt from the chain's guarantee.

### B2. Retention

- [x] **B2.1 — `POST /audit/retention/sweep` endpoint.** Finds records older than
  the configured `RETENTION_WINDOW_DAYS` (global setting) not already archived;
  nulls detail fields (`event_type`, `actor_id`, `resource_type`, `resource_id`,
  `payload`, `payload_field_commitments`, `timestamp`, `recorded_at`); sets
  `archived=true`/`archived_at`; runs through `maintenance_role`.
  **Design point not settled in `REQUIREMENTS.md`, resolved during implementation:**
  eligibility is based on `recorded_at` (server-assigned), not the caller-supplied
  `timestamp` — same trust-boundary reasoning as 7d's chain order and 4a's
  timestamp-role split. A caller reporting a misleading `timestamp` shouldn't be
  able to influence when their own record becomes eligible for archival.
  *Implements: 1a, 1d, 1b, 1c (mechanism).* Depends on: A1.1, A1.3.
  → `src/audit_log_service/services/retention.py`, `src/audit_log_service/api/retention.py`,
  `src/audit_log_service/schemas/retention.py`, `retention_window_days` in
  `core/config.py`.
- [x] **B2.2 — Append `RECORD_ARCHIVED` system event per sweep.** One event per
  sweep *run* (not one per archived record), payload listing every
  `sequence_number` archived — matches the task description's "per sweep" framing.
  Idempotent: a sweep that finds nothing eligible appends no event, and an
  already-archived record is never a candidate again.
  *Implements: 1c (audit trail for the archival action).* Depends on: A2.3, B2.1.
  **Correctness gap found and fixed during implementation, not in the original task
  description:** two concurrent sweep calls could both read the same candidate set
  before either archives it, producing two `RECORD_ARCHIVED` events over the same
  records. Fixed by acquiring `append_event`'s advisory lock explicitly at the top
  of `sweep_retention`, before reading candidates — safe to acquire twice in one
  transaction (Postgres advisory locks are reentrant per-transaction), and fully
  serializes sweeps against each other and against regular appends using the same
  lock key already established in 7c.

  **Verified live against the real stack:** wrote two records, backdated one's
  `recorded_at` to two years ago via direct SQL (simulating age, since `recorded_at`
  is server-assigned and can't be set through the write API), ran the real sweep
  endpoint against the actual default 365-day window — confirmed only the old
  record was archived, the recent one untouched, and a `RECORD_ARCHIVED` event
  correctly appended. Confirmed `/audit/verify` stays intact. Confirmed default
  query exclusion / `includeArchived=true` opt-in. Confirmed idempotency — a second
  sweep call archives nothing. Confirmed `content_hash`/`prev_hash` on the archived
  record are byte-for-byte unchanged. Confirmed via direct `psql` as `app_role` that
  it still cannot perform the archival `UPDATE` — the same negative test pattern as
  A1.3/B1, now validated against this third caller of `maintenance_role`.

### B3. Bulk export

- [x] **B3.1 — Ed25519 signing key setup.** Generate keypair; private key loaded
  from an environment variable/secrets manager at startup, never committed; public
  key documented/exposed for recipients. `signingKeyId` supports future rotation.
  *Implements: 5c (operational note).*
  → `src/audit_log_service/core/signing.py`, `export_signing_key_seed_hex` +
  `export_signing_key_id` in `core/config.py`, `cryptography` added as a dependency.
  Public key exposed via `GET /audit/export/public-key`, not just documentation —
  lets a recipient fetch it directly rather than requiring separate distribution.
- [x] **B3.2 — `GET /audit/export` endpoint.** Requires at least one of
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
  → `src/audit_log_service/services/export.py`, `src/audit_log_service/api/export.py`,
  `src/audit_log_service/schemas/export.py`. Extracted `build_filtered_query` out of
  `services/query.py` so export and the paginated query endpoint share one filter
  implementation rather than two copies that could drift (export defaults
  `include_archived=True`, the opposite of the query endpoint's default, since a
  compliance export is meant to show the complete history, not just the active
  subset). Added `CanonicalDatetime` (a Pydantic `PlainSerializer` type alias in
  `schemas/event.py`, applied to `AuditEventOut` and `ExportBundle`) — a
  correctness requirement identified while building this, not a stylistic choice:
  the signature covers a canonical serialization of the bundle, so whatever the
  JSON *response* actually contains must be byte-identical to what was signed, and
  relying on Pydantic's default datetime formatting to happen to match our own
  `canonical_timestamp` function would have been fragile. The service builds the
  full `ExportBundle` model first, then derives the signable bytes from
  `model_dump(mode="json", by_alias=True, exclude={"signature"})` rather than
  hand-building a parallel dict — guarantees the signed bytes and the response body
  can never drift apart.

  **Verified live against the real stack, including fully independent third-party
  signature verification (not calling any of our own service code)** — wrote three
  events, exported filtered by `resourceId`, fetched the public key from
  `/audit/export/public-key`, then in a separate Python process reconstructed the
  canonical JSON from the bundle's own content and verified the Ed25519 signature
  using only `cryptography`'s primitives directly: **valid**. Tampered a record's
  payload *and* its own `contentHash` together in the downloaded bundle file (the
  exact attack self-consistency checks alone can't catch) and re-verified:
  correctly **invalid**. Redacted a field and re-exported — redaction represented
  correctly, signature still verifies. Confirmed the required-filter validation
  (400 with neither `resourceId` nor `actorId`).

  **Real gap found via live testing, not a bug in this code — a cross-feature
  interaction with Scenario B's retention design:** archiving a record nulls every
  column export can filter on, so an archived record becomes unreachable via
  `actorId`/`resourceType`/`resourceId`/`eventType`/time-range filters — 5e's "yes,
  archived records can be exported" is true in principle but not reachable in
  practice through this endpoint once a record is archived. Raised to the user as a
  three-way decision (document as a known limitation / add sequence-number-range
  export filters / reopen what archival nulls); **user chose to document it**
  rather than change either already-built feature. See `docs/REQUIREMENTS.md`,
  Scenario B item 5e, for the full writeup, and Scenario C's clarified requirement,
  which inherits this limitation.

### B4. Tests

- [x] **B4.1 — Unit: redaction preserves chain validity.** Redact a field, confirm
  `content_hash` unchanged, confirm verify (A4.1) still reports intact, confirm the
  *other* fields in the same payload are still independently tamper-detectable
  (regression test for the whole-payload-hash failure mode identified while
  resolving 3a).
  *Implements: 3a, 3f.*
  → `tests/test_redaction.py`. Also caught a bug in the test suite itself, not the
  app: an early draft asserted `redact_field`'s return value *was* the
  `FIELD_REDACTED` event — it actually returns the redacted target record (which is
  what the API hands back), matching `api/redact.py`'s intent. Fixed by splitting
  into two tests: one confirming the return value, one confirming the event is
  discoverable via `list_events` (3f's actual discoverability answer).
- [x] **B4.2 — Unit: archival preserves chain validity, no false positive.**
  Archive a record, confirm verify reports intact (2b's new branch exercised),
  confirm gap detection is unaffected (2c).
  *Implements: 1d, 2a, 2b, 2c.*
  → `tests/test_retention.py`.
- [x] **B4.3 — Unit: redacted field salting.** Confirm two records with the same
  redacted value produce *different* stored hashes (distinct salts) — salting
  defeats brute-force even for low-entropy values like a 9-digit account number —
  regression test for the brute-force risk identified in 3a.
  → Covered at the pure-function level in `tests/test_hashing.py`
  (`test_field_hash_differs_for_different_salts`, written alongside A2.1) rather
  than duplicated as a DB-backed integration test — the property doesn't depend on
  the database.
- [x] **B4.4 — Integration: export bundle signature.** Tamper with an exported
  bundle's JSON (any field, including a record's own `content_hash`), confirm
  signature verification fails — the scenario the rechaining-vs-signature analysis
  (5a) was specifically evaluating.
  → `tests/test_export.py`. Verifies independently using `cryptography`'s
  primitives directly (not calling the app's own `sign`/`verify` code), mirroring
  the live third-party verification done manually during B3.
- [x] **B4.5 — Integration: DB-level immutability.** Confirm `app_role`'s connection
  gets a permission error on any attempted `UPDATE`/`DELETE` against `audit_events`
  — proves 2a's defense-in-depth layer is real, not just documented intent.
  → `tests/test_privileges.py`. Also confirms the inverse (`maintenance_role` *can*
  update `payload`) — without a positive case, the negative tests could pass
  vacuously if the role had no access to the database at all.

---

## Scenario C — Compliance Reporting

No new endpoint or schema — the entire technical design is captured in B3.2's
`eventType`/`from`/`to` filter extension. What's left is validation and
documentation specific to the compliance use case.

- [x] **C1 — Documentation: compliance reporting usage guidance.** README/
  architecture-doc section walking through the compliance use case end-to-end:
  e.g. "filter by `resourceId=<account>` + `eventType=ACCOUNT_VIEWED` + `from`/`to`
  to produce a regulator-ready, independently-verifiable access report," including
  the explicit scope boundaries from the clarified requirement (no auth/authz on
  who can call this, no guarantee every read-path in a broader system emits an
  access event, no payload-content search).
  *Implements: the Clarified Requirement Statement, Scenario C.*
  → New "Compliance reporting (Scenario C)" section in `README.md`, including the
  archived-record export limitation found during B3 (Scenario B item 5e).
- [x] **C2 — Integration test: compliance report scenario.** Write a mix of
  `ACCOUNT_VIEWED` and other event types for the same `resourceId`; export filtered
  by `resourceId` + `eventType=ACCOUNT_VIEWED` + a time range that excludes some of
  the written events; confirm only the matching subset appears in the bundle and
  the signature verifies.
  *Implements: C1–C6, the Clarified Requirement Statement.* Depends on: B3.2.
  → `tests/test_compliance.py`. Three events: one access event in-window (included),
  one non-access event in-window (excluded by `eventType`), one access event
  out-of-window (excluded by `from`/`to`) — confirms the filter combination actually
  isolates the intended subset, not just that filtering works in isolation.
  Independently verifies the signature via `cryptography` directly, same pattern as
  `test_export.py`.

**C1–C2 (the original Scenario C scope) are complete.** C6's original "auth is out
of scope" position was reopened post-review (see `REQUIREMENTS.md`'s "Requirement
Change — Authentication & Authorization"); C3 below implements that change (C7–C12).

**Still explicitly out of scope (from the Clarified Requirement Statement, not
omissions):** instrumentation guaranteeing every application read-path emits an
access event (not this service's responsibility); full-text/payload-content search
for account data referenced inside non-account-`resourceType` records (C3 —
partially mitigated by independent `actorId`/time-range filtering, not solved);
compliance with any specific named regulation (C5); a human-formatted report
document (PDF/CSV) — the deliverable is the verifiable JSON bundle itself.

### C3. Authentication & Authorization

Implements the post-review requirement change (`REQUIREMENTS.md` C7–C12): every
endpoint except `GET /health` and `GET /audit/export/public-key` now requires a
valid API key and role; `compliance`/`reader` principals can additionally be scoped
to specific accounts.

- [ ] **C3.1 — API key / principal configuration.** New `Principal` type
  (`principal_id`, `roles: set[str]`, `resource_scope: list[str] | None`) and a
  dev-fixed `Settings.api_keys: dict[str, Principal]` config — same pattern already
  used for the export signing key seed and DB role passwords: fixed, documented, no
  secrets-manager integration.
  *Implements: C7, C11.*
  → `src/audit_log_service/core/config.py`, new `src/audit_log_service/core/auth.py`.
- [ ] **C3.2 — Authentication + role-requirement dependency.** `require_roles(*roles)`
  FastAPI dependency factory reading the `X-API-Key` header: `401` on a missing or
  unrecognized key, `403` on a valid key lacking every required role. Wired into
  every router per C9's role table.
  *Implements: C7, C8, C9.* Depends on: C3.1.
  → `src/audit_log_service/core/auth.py`; `api/events.py`, `api/verify.py`,
  `api/redact.py`, `api/retention.py`, `api/export.py`.
- [ ] **C3.3 — Derive `actorId` from the authenticated principal for redact/
  retention.** Remove `actorId` from `RedactRequest`/`RetentionSweepRequest`;
  `redact_field`/`sweep_retention` take the actor id from the authenticated
  `Principal`, not the request body. `POST /audit/events`'s `actorId` is
  unaffected — stays caller-supplied per C10's reasoning.
  *Implements: C10.* Depends on: C3.2.
  → `schemas/redact.py`, `schemas/retention.py`, `services/redact.py`,
  `services/retention.py`, `api/redact.py`, `api/retention.py`.
- [ ] **C3.4 — Resource-scope enforcement for query and export.**
  `build_filtered_query`/`export_bundle` gain a `resource_scope: list[str] | None`
  parameter, intersected directly into the SQL filter — never merely checked
  against caller-supplied parameters, so a scoped caller can't see other accounts
  by omitting a `resourceId` filter. A `resourceId` named outside the caller's
  scope is denied with `404`, not `403`.
  *Implements: C12.* Depends on: C3.2.
  → `services/query.py`, `services/export.py`, `api/events.py`, `api/export.py`.
- [ ] **C3.5 — Tests: auth/authz enforcement.** Per-endpoint negative coverage
  (missing key → `401`, invalid key → `401`, valid key/wrong role → `403`) plus
  positive coverage confirming each role's permitted endpoints still work end to
  end. Updates two existing test surfaces for the breaking changes this
  introduces: `test_http.py`'s HTTP calls need an `X-API-Key` header, and
  redaction/retention tests stop passing `actorId` in the request body.
  *Implements: C7–C10.* Depends on: C3.2, C3.3.
  → New `tests/test_auth.py`; `tests/conftest.py`'s `client` fixture gains a way
  to issue a request under a given role/principal.
- [ ] **C3.6 — Test: cross-account denial.** Two `compliance`-scoped API keys,
  each with a `resourceScope` limited to a different `resourceId`. Each
  successfully queries/exports its own account; each is denied (`404`) on the
  other's account — both when naming it explicitly and when omitting the filter
  entirely, confirming enforcement happens server-side rather than only against
  what the caller thought to ask for.
  *Implements: C12.* Depends on: C3.4.
  → New `tests/test_cross_tenant.py`.
- [ ] **C3.7 — Documentation.** `ARCHITECTURE.md`: add the auth/authz layer to the
  components diagram and a new "Security model" subsection explaining how it
  composes with the existing DB-role layer (C9's rationale). `README.md`: new
  "Authentication" section (how to use an API key, the role table) and every
  existing "Using the API" `curl` example updated — `X-API-Key` header added,
  `actorId` removed from the redact/retention examples. `docs/TESTING.md`
  coverage table gains the new test file(s).
  *Implements: C7–C12 (documentation).* Depends on: C3.1–C3.6.
  → `docs/ARCHITECTURE.md`, `README.md`, `docs/TESTING.md`.

---

## Production-readiness extensions

Not part of the original three-scenario scope — added afterward, directed by the
user, to close gaps `TESTING.md`/`ARCHITECTURE.md` had already named as accepted
limitations rather than to satisfy a numbered requirement. Prioritized from a
longer menu (CI pipeline, structured logging, secrets externalization, consistent
error handling were sidelined for now).

- [x] **P1 — HTTP-layer integration tests.** Every other DB-backed test calls
  service functions directly; nothing exercised FastAPI's actual routing, request
  validation, dependency injection, or response serialization. Built via
  `app.dependency_overrides` pointing `get_session`/`get_maintenance_session` at
  the test container, rather than environment-variable manipulation (the app's
  `core/db.py` engines are module-level singletons bound at import time — see
  `conftest.py`'s existing note on why the Alembic migration step already avoids
  touching them).
  → New `client` fixture in `tests/conftest.py`; `tests/test_http.py` — one test
  per endpoint's HTTP-specific behavior (status codes, response shape), plus one
  full req 9 acceptance flow through the real HTTP surface end to end (write →
  query → verify → direct-datastore tamper via the admin connection → verify
  catches it) — the one test proving the pieces are wired together correctly as a
  whole, not just individually correct. 11 tests, all passed on the first run
  against real Postgres (the underlying HTTP wiring had already been extensively
  live-verified via `curl` during each feature's development).
- [x] **P2 — Write-throughput load test.** Turns 7c's "fully serialized appends"
  from a qualitative, accepted trade-off into a measured number — closes the
  "Load/scale testing" gap named in `TESTING.md`.
  → `tests/test_load.py`. 100 concurrent writes via the real HTTP layer (building
  on P1's `client` fixture); asserts correctness (every write succeeds, the
  resulting chain is gapless and verifies intact) rather than a hard throughput
  threshold — hardware varies too much across machines/CI for a fixed number to be
  a meaningful pass/fail gate without becoming flaky. Reports throughput as
  informational output instead. **Measured: ~55 writes/sec** in this environment
  (see `docs/TESTING.md` for the number and what it does/doesn't measure — ASGI
  in-process transport, so this is application+DB serialization overhead, not
  wire-level HTTP cost).
- [ ] **P3 — Fail-secure guard on hardcoded dev secrets.** Raised in code review:
  `Settings` (`app_role_password`, `maintenance_role_password`,
  `export_signing_key_seed_hex`, and the passwords embedded again in
  `database_url`/`maintenance_database_url`/`admin_database_url`) hardcodes real
  secret material as Python defaults with nothing checking whether a deployment
  actually overrode them — the app boots and signs export bundles with the exact
  values sitting in this repo if someone forgets to. A narrower fix than full
  secrets externalization (still sidelined below — no vault/KMS integration):
  add `Settings.environment: Literal["development", "test", "production"] =
  "development"`, and a `model_validator` that raises at startup if
  `environment == "production"` and any of the three secret fields still equal
  their hardcoded dev-default literal. Local dev/test behavior is unchanged
  (default stays `"development"`); the only new behavior is refusing to boot
  under `ENVIRONMENT=production` with unoverridden secrets — fails closed instead
  of silently starting.
  *Implements: fix for the code-review finding "hardcoded dev secrets with no
  fail-secure guard."*
  → `src/audit_log_service/core/config.py`; new test asserting the raise under
  `environment="production"` with default secrets, and that it doesn't fire in
  `"development"` or with genuinely overridden secrets.

**Explicitly sidelined, not forgotten:** CI pipeline (`ruff`/`mypy`/`bandit`/
`pip-audit`/`pytest` enforced on every push, currently only run manually),
structured logging/observability, full secrets externalization (P3 adds a
fail-secure *guard*, not sourcing secrets from an actual manager/KMS — that
remains out of scope), consistent global error-handling schema, and
verify-walk-latency-at-scale testing (only write throughput was measured, not the
O(n) verify walk's behavior at realistic chain lengths).

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
4. **C1/C2** — pure documentation and a validation test against B3.2, no new
   implementation.
5. **C3.1 → C3.2 → (C3.3, C3.4 in parallel) → C3.5 → C3.6 → C3.7** — added after
   the rest was complete, per the post-review requirement change. C3.3 (actorId
   derivation) and C3.4 (resource scoping) don't depend on each other, only on
   C3.2's dependency existing; C3.6 needs C3.4's scoping logic in place before a
   denial test has anything to assert against; C3.7 last since it documents the
   finished behavior rather than driving it.
