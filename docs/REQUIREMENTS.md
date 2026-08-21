# Requirements & Design Decisions

Living record of each scenario's raw requirements, the ambiguities identified against
them, and the decisions made to resolve each one — with rationale and accepted
trade-offs, so any decision can be revisited if an assumption changes. One section per
scenario. Each scenario's resolved list feeds a matching section in `TASKS.md`.

Source: `instructions.pdf` (not committed — see `.gitignore`).

---

## Scenario A — Core Audit Log Service

### Raw Requirements

1. **Event ingestion schema** — Write API accepts `eventType`, `actorId`, `resourceType`,
   `resourceId`, `payload` (structured, event-specific), `timestamp`.
   *Intent:* a minimally sufficient, structured description of who/what/when/detail for
   every auditable action, without a fixed schema per event type.

2. **Append-only enforcement** — No update or delete operation exposed by the API.
   *Intent:* immutability enforced at the interface boundary, not just by convention.

3. **Timestamp source** — caller-supplied or server-assigned; document the choice.
   *Intent:* explicit acknowledgment this is a judgment call with trust implications.

4. **Query/filtering** — filter by any combination of `actorId`; `resourceType` +
   `resourceId`; `eventType`; time range (`from`/`to`).
   *Intent:* support real investigative workflows, combinable filters.

5. **Pagination** — support pagination for large result sets.
   *Intent:* scale to realistic log volumes without unbounded payloads.

6. **Hash chain — content hash** — each record includes a hash of its own content.
   *Intent:* detect single-record content tampering.

7. **Hash chain — linkage** — each record includes a hash of the immediately preceding
   record, or a defined genesis value for the first record.
   *Intent:* chain records into an ordered sequence so tampering invalidates everything
   downstream.

8. **Chain verification endpoint** — `GET /audit/verify` walks the full chain; reports
   intact/not, and if not, the first inconsistent record and violation type.
   *Intent:* on-demand integrity attestation with the ability to localize a break.

9. **Acceptance/validation method** — write → query → verify → directly mutate a record
   in the datastore → verify again to confirm detection. No external consumer required.
   *Intent:* defines "done" operationally; implies the datastore must allow the direct
   mutation to succeed (detection relies on the hash chain, not DB-level prevention).

### Ambiguities & Decisions

Grouped by dependency — later groups build on decisions made in earlier ones. Worked
top-to-bottom.

#### Group 1 — Foundational architecture

##### 7a — Chain scope: single global chain vs. per-resource/actor chains
- **Status:** ✅ Decided
- **Decision:** Single global chain across all events.
- **Rationale:**
  - Matches the literal spec: "the chain" (singular), and `GET /audit/verify` is
    unparameterized and walks "the full chain."
  - Stronger tamper-evidence: per-resource chains would let an attacker delete an
    entire resource's history without any other chain detecting it. One global
    sequence means removing any record, anywhere, breaks the one sequence everything
    else depends on.
  - Matches how tamper-evident logs are conventionally built — one monotonic sequence
    as a single source of truth.
- **Trade-offs accepted:**
  - Scenario B's bulk export (filtered by `resourceId`/`actorId`) will be a
    non-contiguous slice of the global chain. The bundle can prove per-record content
    integrity via each record's own hash, but not sequence/linkage integrity purely
    from the exported subset — needs its own design in Scenario B (documented
    limitation, or an inclusion-proof scheme).
  - All writes serialize against one shared chain tail — requires a concurrency design
    (see 7c) to stay correct under concurrent writers.
  - `/audit/verify` cost grows with total system-wide event volume, not per-resource
    volume — relevant to 7e/8b and to Scenario B retention/archiving.

##### 1a–1d — Event schema shape
- **Status:** ✅ Decided
- **1a. `eventType` — free-form vs. enum:** Free-form string, format-constrained
  (non-empty, pattern `^[A-Z][A-Z0-9_]*$`). No enum/registry — the service must ingest
  new event types from arbitrary producers without a schema migration; the pattern
  constraint just prevents casing/format fragmentation that would silently split
  `eventType` query filters.
- **1b. Are all six fields mandatory?** Yes, all six mandatory (non-null; strings
  non-empty). Reading "containing **at minimum**: [6 fields]" as these six being the
  floor, not optional — reinforced by every example event (`USER_LOGIN`,
  `RECORD_UPDATED`, `PERMISSION_GRANTED`) having a natural resource and actor.
  `actorId` may represent a system/service actor (e.g. `"system:scheduled-job"`) for
  automated events, but is always populated.
- **1c. `payload` limits:** Must be a JSON *object* (not array/scalar) — implied by
  "structured object" and required for Scenario B's field-level redaction to make
  sense. Bounded by a serialized-size cap (~32KB) and max nesting depth (~10),
  primarily to bound hashing/canonicalization cost and avoid a pathological-JSON
  processing vector. Exact constants are tunable, not architecturally significant.
- **1d. Invalid input handling:** Validation (Pydantic, API boundary) happens before
  any chain-mutating logic. A rejected write never receives a sequence number, never
  computes a hash, has no persistence side effect — guarantees invalid writes can't
  introduce gaps in the chain sequence (keeps 8a's gap-detection semantics meaningful).

##### 3a/3b — Timestamp source
- **Status:** ✅ Decided
- **Decision:** Keep both a caller-supplied and a server-assigned timestamp, with
  different roles:
  - `timestamp` — caller-supplied, mandatory (per 1b). Represents "when the event
    occurred" from the source system's perspective. Often the only accurate record
    when there's ingestion delay (batching, retries, offline queuing) between real
    occurrence and arrival here.
  - `recordedAt` — server-assigned at write time, never caller-controlled, monotonic.
- **Rationale:** A purely caller-supplied timestamp is attacker-controllable input —
  unacceptable as the basis for chain order or integrity in a system whose purpose is
  trustworthy record-keeping (and that Scenario C leans on for compliance reporting). A
  purely server-assigned timestamp throws away real information investigators usually
  want to query by. Keeping both lets each serve the role it's actually trustworthy for.
- **Trade-off accepted:** a caller can still report a misleading `timestamp`, and a
  time-range query could miss such an event as a result. This is a data-quality/
  trust-of-content problem, not a tamper-evidence problem — the hash chain guarantees a
  record wasn't altered or removed after the fact, not that the caller told the truth
  about when it happened. Documented as a known limitation rather than something the
  chain is expected to catch.

#### Group 2 — Chain mechanics (depend on Group 1)

##### 7d — Chain order: insertion sequence vs. `timestamp`
- **Status:** ✅ Decided (resolved as part of 3a/3b)
- **Decision:** Chain order = insertion order = the server-assigned `recordedAt`,
  assigned atomically alongside the sequence number. Immune to caller manipulation —
  closes the exact gap a caller-controlled ordering field would open.

##### 7c — Concurrency enforcement for concurrent appends
- **Status:** ✅ Decided
- **Decision:** Serialize the append critical section (read tail → compute
  `sequence_number`/`prevHash` → compute this record's hash → insert → commit) with a
  Postgres advisory transaction lock (`pg_advisory_xact_lock`) on a fixed constant key.
  `sequence_number` is an application-computed `BIGINT` (`tail_seq + 1`), not a DB
  `SERIAL`/`IDENTITY`, backed by a `UNIQUE` constraint on `sequence_number` as a
  defense-in-depth backstop.
- **Rationale:**
  - Two concurrent writers reading the same tail before either commits would both
    link to it, producing a forked/invalid chain — needs a concrete serialization
    mechanism, not just careful application code.
  - `SERIALIZABLE` isolation + optimistic retry was considered and rejected: if
    `sequence_number` came from a DB sequence object, an aborted/retried transaction
    permanently burns that value (sequences aren't transactional), leaving a gap
    indistinguishable from one caused by malicious deletion — undermines 8a's
    gap-detection semantics.
  - Advisory lock (vs. `SELECT ... FOR UPDATE` on the last row) chosen specifically for
    the bootstrap/genesis case: locking a specific row doesn't work when the table is
    empty, while a lock on a constant key behaves identically whether the chain has
    zero records or a million.
  - Computing `sequence_number` in the application (only ever consumed by a commit
    that actually happens) rather than via a DB sequence is a prerequisite for 8a's
    gap-detection to be sound — a gap can only mean tampering, never a benign retry.
- **Trade-off accepted:** fully serializes writes — one append at a time, system-wide.
  This is the direct, already-accepted consequence of 7a's single-global-chain
  decision, not a new cost. Documented as a known scalability limit; a production
  system at higher write volume would likely move to batched/checkpointed hashing
  (e.g. periodic Merkle roots over batches) — out of scope here.

##### 7b — Genesis value definition
- **Status:** ✅ Decided (resolved as part of 6a–6c, depends on 6a's output encoding)
- **Decision:** `"0" * 64` — 64 zero characters. Given real SHA-256 output is
  practically never all-zero, this is an unambiguous, easily documented sentinel.
  `/audit/verify` checks the first record's (`sequence_number = 1`) `prevHash` against
  this exact constant.

##### 6a–6c — Hash algorithm + canonical content definition
- **Status:** ✅ Decided
- **6a. Hash algorithm:** SHA-256, output as a 64-char lowercase hex string (`CHAR(64)`
  column), not raw bytes. MD5 and SHA-1 explicitly ruled out (practical collision
  attacks — SHA-1's 2017 SHAttered attack, MD5 trivially broken); something like
  BLAKE3 would also be secure but there's no performance bottleneck here to justify
  deviating from the more universally recognized standard. Hex over raw bytes because
  JSON has no native binary type — a `bytea` column would need base64 re-encoding for
  any API response (e.g. verify's error output), while hex is directly human-comparable
  end-to-end.
- **6b. What counts as "content":** every persisted field of the record *except* the
  hash fields — the six caller-supplied fields **plus** the server-assigned
  `sequence_number` and `recordedAt`. Not just the caller-facing six: requirement 9's
  validation test is a direct-datastore mutation bypassing the API, and if
  `sequence_number`/`recordedAt` weren't covered, an attacker with datastore access
  could silently resequence or retime a record without invalidating anything.
  `prevHash` is deliberately **excluded** from the content hash — kept as a separate
  stored field pointing at the previous record's content hash, so "was this record's
  own data altered" (content-hash check) stays cleanly separable from "was this
  record's position in the chain altered" (link check). This separation is what makes
  8a's violation taxonomy possible.
- **6c. Canonical serialization:** canonicalize the *parsed Python value*, not raw
  request bytes — recursively sort all object keys, fixed separators, no whitespace
  (`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`),
  UTF-8 encoded before hashing. Applied uniformly to the whole content structure
  (all eight fields, including recursively inside `payload`) via one function, not two
  mechanisms for schema fields vs. payload. Timestamps normalized to canonical ISO-8601
  (fixed precision, UTC `Z` suffix) at the schema layer before reaching the hash
  function.
  - **Why parsed value, not raw bytes:** Postgres JSONB does not guarantee byte-for-byte
    round-tripping of submitted JSON text (can reformat numbers, doesn't preserve exact
    whitespace). Hashing raw bytes at write time and re-deriving from raw bytes read
    back at verify time would risk a false-positive tamper detection from a purely
    cosmetic storage reformat. Regenerating the canonical string from the logical value
    both directions avoids this, since JSONB does preserve logical structure faithfully.
  - **Known test to write, not a design gap:** numeric int-vs-float fidelity through the
    JSONB round-trip (e.g. `100` vs `100.0` in a payload) — needs an explicit test
    rather than an assumption.

##### 6d + 8a — Self-hash vs. combined block hash, paired with verify's violation taxonomy
- **Status:** ✅ Decided
- **6d. Combined `recordHash`, not two independent fields:** with `contentHash` and
  `prevHash` as two independent stored fields (6b/6c), a modification to record N-1
  only requires the attacker to also update record N's `prevHash` to match — since
  `contentHash(N)` doesn't depend on `prevHash(N)`, record N's own hash never changes,
  so record N+1 (which only ever referenced `contentHash(N)`) still validates. A
  two-record forgery rewrites history with nothing downstream detecting it —
  contradicting req 7's explicit guarantee ("invalidates its own hash and every hash
  that follows it"). Fix: define
  ```
  recordHash(N) = SHA256(contentHash(N) || prevHash(N))
  ```
  **not a stored column** — computed on demand. The next record's `prevHash` points to
  this value: `prevHash(N+1) = recordHash(N)`. Now `prevHash(N)` is an input to
  `recordHash(N)`, so any downstream forgery cascades: covering a tampered record
  requires re-forging every record after it, all the way to the tail.
- **8a. Violation taxonomy — three categories, not four:**
  1. `GENESIS_MISMATCH` — record `sequence_number=1`'s `prevHash` ≠ `"0"*64`.
  2. `CONTENT_MISMATCH` — a record's stored `contentHash` doesn't match a fresh
     recomputation from its own stored fields.
  3. `LINK_MISMATCH` — a record's `prevHash` doesn't match `recordHash` of the record
     with the next-lower *existing* `sequence_number`. If that gap is >1, the reported
     detail names which `sequence_number`(s) are missing (sub-classification for
     diagnostic quality); otherwise reported as a plain link break (predecessor exists
     but hash doesn't match).
  - **Explicitly rejected a fourth, independent `SEQUENCE_GAP` check** — walked through
    with the user: in the realistic tampering case (a single direct mutation, per req
    9's validation test), a deleted record already produces a `LINK_MISMATCH` when
    "previous" is resolved dynamically against currently-existing records. An attacker
    who additionally rewrites the neighbor's `prevHash` to "heal" the gap doesn't evade
    detection either — `prevHash(N)` feeding `recordHash(N)` (per 6d) just relocates
    the mismatch one record further down the chain. The only way to produce zero link
    mismatches is to recompute and rewrite every record from the edit point to the
    current tail (sequence numbers included, since they're inside `contentHash` per
    6b) — which a gap check wouldn't catch either. **Documented limitation, not solved
    here:** hash chains without external anchoring (periodically publishing the tail
    hash outside the system) can't protect against a fully-resourced attacker willing
    to rewrite an entire consistent alternate tail. Flag for the risk/limitations
    write-up; out of scope to solve in this prototype.

##### 8b + 7e — Fail-fast vs. full report; synchronous vs. streaming chain walk
- **Status:** ✅ Decided
- **8b. Fail-fast:** verify stops at the first inconsistency and reports that record +
  its violation type; it does not continue walking to compile a full list of every
  subsequent issue. Matches the requirement's literal wording ("which record is **the
  first** inconsistency," singular). Also: once the chain breaks at record N, every
  record after N is only "invalid" in the derivative sense of no longer connecting to
  a valid history — continuing to individually flag them adds complexity without
  adding information the requirement asked for. A full "show everything that's wrong"
  forensic mode is a reasonable future addition, not built now since nothing asked
  for it.
- **7e. Synchronous walk, DB-streamed rather than buffered:** a single synchronous
  HTTP request, not an async job/polling model or incremental/checkpointed
  verification — those are real production patterns but over-engineering for this
  assignment's actual validation flow (write a handful of events, verify, tamper one,
  verify again) and 2–3 day scope. Note fail-fast (8b) only shortens the *broken* case
  — an intact chain still requires inspecting every record, so this stays O(n)
  regardless. The one discipline applied regardless of scale: iterate records via a
  streaming DB cursor rather than loading the full table into memory, keeping memory
  O(1) even though wall-clock time is O(n).
- **Documented limitation:** for a chain large enough that a full synchronous walk
  becomes slow, a production version would want incremental verification anchored to
  a periodically cached "last known-good position," or an async job model — noted as
  future work, out of scope here.

#### Group 3 — Query/pagination (depend on chain scope + timestamp choice)

##### 5a/5d — Pagination strategy & stability under concurrent writes
- **Status:** ✅ Decided
- **Decision:** Cursor/keyset pagination anchored to `sequence_number` (7c's gapless,
  strictly increasing `BIGINT`): `WHERE <filters> AND sequence_number <cmp> :cursor
  ORDER BY sequence_number LIMIT :limit`, returning a `nextCursor` in the response.
  Cursor exposed as the raw `sequence_number` value, not an opaque encoded token —
  it's a single monotonic integer with nothing sensitive to hide, so token engineering
  (base64, versioning) would be complexity without a reason.
- **Rationale:** Offset pagination can skip or duplicate rows on an append-only table
  under continuous inserts. `sequence_number > X` (or `<`, per 5b) means the same thing
  regardless of which filters (req 4) are applied alongside it, so this composes
  cleanly with arbitrary filter combinations — offset pagination gives no equivalent
  guarantee.

##### 5b — Sort order
- **Status:** ✅ Decided
- **Decision:** Descending (`sequence_number DESC`) — newest first.
- **Rationale:** User's call, weighing this as a genuine (if low-stakes) trade-off
  between matching chain/insertion order (ascending) vs. matching how audit/
  investigation tools typically default (most recent activity first) and fitting
  Scenario C's regulator-investigation framing. Query mechanics are symmetric either
  way (`sequence_number < :cursor ORDER BY sequence_number DESC`), so this was a UX
  preference, not a technical constraint.

##### 5c — Page size / cap
- **Status:** ✅ Decided
- **Decision:** Default `limit=50`, max `limit=500`, enforced via Pydantic validation
  (`Query(50, ge=1, le=500)`). Tunable constants, not architecturally significant.

##### 4a — Time-range filter target
- **Status:** ✅ Decided (resolved as part of 3a/3b)
- **Decision:** The public `from`/`to` filter operates on the caller-supplied
  `timestamp`, since that's the semantic "when did this happen" most investigative
  queries actually want, and matches how most audit/log systems filter.
  Server-assigned `recordedAt` stays internal to chain mechanics for Scenario A — not
  exposed as a second filter dimension. Nothing in the doc asks for filtering by
  ingestion time, so this keeps scope tight.

##### 4b — Filter AND/OR semantics
- **Status:** ✅ Decided
- **Decision:** AND. Multiple supplied filters narrow the result set
  (`actorId=X AND eventType=Y`), not broaden it. Matches "filter by any combination of"
  and the near-universal REST filtering convention — no real case for OR here.

##### 4c — Is `resourceType` alone (without `resourceId`) a valid filter?
- **Status:** ✅ Decided
- **Decision:** Yes. `resourceType` and `resourceId` are independent optional filters,
  AND'ed together when both are given (per 4b) — not a rigid "must supply both or
  neither" pair.
- **Rationale:** The doc pairs them textually ("resourceType and resourceId") but
  doesn't forbid `resourceType` alone, and there's concrete forward value: Scenario C's
  framing ("regulators need to audit access to client account data") is exactly a
  "show me everything affecting any resource of type ACCOUNT" query — `resourceType`
  without a specific `resourceId`. Natural reading of "any combination of."

#### Group 4 — Hardening (additive, doesn't block core design)

##### 2a — DB-level immutability enforcement alongside hash-chain detection
- **Status:** ✅ Decided
- **Decision:** Least-privilege DB role for the application's runtime connection —
  `GRANT SELECT, INSERT ON audit_events TO app_role`, with `UPDATE`/`DELETE` and all
  DDL never granted. Migrations run under a separate owner/migration role, not the
  app's runtime role.
- **Rationale:** Resolves an apparent tension with req 9's validation test (which
  requires a direct datastore mutation to *succeed*, so verify has something to catch)
  by recognizing that test only needs *some* privileged connection, not the app's own
  runtime credentials. Two layers of defense, for two different threats:
  - **Application-layer bugs/injection** — prevented outright; the app's own
    credentials physically can't issue `UPDATE`/`DELETE` against this table.
  - **A privileged actor with direct database access** (DBA, compromised superuser
    credential) — can't be prevented by a role grant (whoever controls roles can
    re-grant access), which is exactly the threat the hash chain is built to *detect*
    rather than prevent, and exactly what req 9's test simulates: bypassing the
    application (and its restricted role) entirely via a more privileged connection.
- **Forward-looking note, not resolved now:** Scenario B's redaction and
  retention/archival features will need to decide how they interact with this
  restriction — either a separate, narrowly-scoped elevated role for those specific
  operations, or an append-only/tombstone design that never needs `UPDATE` at all.

### Next steps

Scenario A's ambiguity list is fully resolved — every item across all four groups is
Decided. Next: translate this into `docs/TASKS.md`.

---

## Scenario B — Retention & Redaction

_Not yet started._

---

## Scenario C — Compliance Reporting

_Not yet started._
