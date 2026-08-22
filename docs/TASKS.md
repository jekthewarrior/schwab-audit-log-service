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

- [ ] **A1.1 — `audit_events` SQLAlchemy model.** Columns: `sequence_number`
  (`BIGINT`, app-computed, `UNIQUE`), `event_type`, `actor_id`, `resource_type`,
  `resource_id`, `payload` (`JSONB`), `payload_field_commitments` (`JSONB`:
  `{fieldName: {hash, salt}}`), `timestamp` (caller-supplied), `recorded_at`
  (server-assigned), `content_hash` (`CHAR(64)`), `prev_hash` (`CHAR(64)`),
  `archived` (`BOOLEAN`, default `false`), `archived_at` (nullable). Built with
  Scenario B's redaction (per-field commitments) and archival (flag columns) needs
  included from the start — avoids a schema-migration redo later.
  *Implements: 1a–1d, 3a/3b (Scenario A); 3a (Scenario B, payload commitment
  structure); 1d (Scenario B, archival columns).*
- [ ] **A1.2 — Alembic migration.** Creates the table above, indexes on `actor_id`,
  (`resource_type`, `resource_id`), `event_type`, `timestamp` (query filter
  support), and the `UNIQUE` constraint on `sequence_number`.
  *Implements: 7c, 4a–4c.* Depends on: A1.1.
- [ ] **A1.3 — DB roles/grants migration.** `app_role`: `SELECT`, `INSERT` only, no
  `UPDATE`/`DELETE`/DDL. `maintenance_role`: column-level `UPDATE` on
  `payload`, `payload_field_commitments`, `event_type`, `actor_id`,
  `resource_type`, `resource_id`, `timestamp`, `recorded_at`, `archived`,
  `archived_at` — **never** on `sequence_number`, `content_hash`, `prev_hash`.
  *Implements: 2a (Scenario A); DB privilege (Scenario B, Group 3).* Depends on:
  A1.1.

### A2. Write path

- [ ] **A2.1 — Canonical serialization + hashing module.** Per-field salted
  commitment for `payload` (`fieldHash(key) = SHA256(salt || canonical(key) ||
  canonical(value))`, fresh random salt per field; `payloadCommitment =
  SHA256(canonical(sorted (fieldName, fieldHash) pairs))`); combined `content_hash`
  over the seven non-payload fields plus `payloadCommitment`.
  *Implements: 6a–6c (as amended by Scenario B 3a).*
- [ ] **A2.2 — `recordHash` derivation utility.** Transient, not stored:
  `SHA256(content_hash || prev_hash)`. Used by both the append service (A2.3) and
  verify (A4.1).
  *Implements: 6d.* Depends on: A2.1.
- [ ] **A2.3 — Append service.** Advisory-lock-scoped critical section
  (`pg_advisory_xact_lock` on a fixed constant key): read tail →
  `sequence_number = tail_seq + 1` → `prev_hash = recordHash(tail)` (or genesis
  `"0"*64` if no tail exists) → compute `content_hash` → insert → commit.
  *Implements: 7c, 7d, 7b.* Depends on: A2.1, A2.2, A1.2.
- [ ] **A2.4 — Pydantic request/response schemas.** `eventType` pattern
  `^[A-Z][A-Z0-9_]*$`; all six fields required; `payload` constrained to a JSON
  object with size (~32KB) and nesting-depth (~10) caps.
  *Implements: 1a–1d.*
- [ ] **A2.5 — `POST /audit/events` endpoint.** Wires A2.4 validation → A2.3 append
  service. No update/delete routes exist anywhere in the API.
  *Implements: req 1, req 2.* Depends on: A2.3, A2.4.

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

- [ ] **A5.1 — Unit: canonical serialization determinism** (sorted keys, fixed
  separators reproduce identical output across runs) and the numeric int/float JSONB
  round-trip case flagged in 6c (e.g. `100` vs `100.0`).
- [ ] **A5.2 — Unit: `recordHash` cascade.** Confirms tampering with any single
  record's `content_hash` or `prev_hash` invalidates every subsequent record's link
  — the core security property identified while resolving 6d.
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
