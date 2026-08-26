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
  UTF-8 encoded before hashing. Applied to the seven non-`payload` content fields
  (`sequence_number`, `recordedAt`, `eventType`, `actorId`, `resourceType`,
  `resourceId`, `timestamp`) as one combined value, same as before. Timestamps
  normalized to canonical ISO-8601 (fixed precision, UTC `Z` suffix) at the schema
  layer before reaching the hash function.
  - **Why parsed value, not raw bytes:** Postgres JSONB does not guarantee byte-for-byte
    round-tripping of submitted JSON text (can reformat numbers, doesn't preserve exact
    whitespace). Hashing raw bytes at write time and re-deriving from raw bytes read
    back at verify time would risk a false-positive tamper detection from a purely
    cosmetic storage reformat. Regenerating the canonical string from the logical value
    both directions avoids this, since JSONB does preserve logical structure faithfully.
  - **Known test to write, not a design gap:** numeric int-vs-float fidelity through the
    JSONB round-trip (e.g. `100` vs `100.0` in a payload) — needs an explicit test
    rather than an assumption.
  - **⚠️ REVISED under Scenario B (see Scenario B, item 3a):** `payload` is
    **no longer** folded into `contentHash` as a single flat canonicalized blob. A flat
    hash over concatenated data is an all-or-nothing commitment — there's no way to
    prove a subset of its input is unchanged without possessing the complete original,
    which makes selective field redaction (Scenario B) impossible without destroying
    verifiability of every *other* field in the payload too. `payload` is instead
    hashed as a **per-field salted commitment structure**:
    `fieldHash(key) = SHA256(salt || canonical(key) || canonical(value))` for each
    top-level field, with `payloadCommitment = SHA256(canonical(sorted
    (fieldName, fieldHash) pairs))` substituted for the raw payload value as
    `contentHash`'s eighth input. Full rationale, the redaction mechanism this enables,
    and why salting is required (low-entropy values like SSNs/account numbers are
    brute-forceable from an unsalted hash) are in Scenario B, item 3a — not
    duplicated here to avoid the two copies drifting out of sync.

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

### Raw Requirements

1. **Retention policy** — records older than a configurable window should be
   archivable or soft-deletable.
   *Intent:* support data lifecycle management / compliance-driven retention windows
   without literally deleting audit history in a way that breaks tamper-evidence.

2. **Verify must not false-positive on archived records** — `/audit/verify` must
   handle the presence of archived records correctly, without reporting a false
   positive break for records legitimately archived per policy.
   *Intent:* archiving is sanctioned, policy-driven, and distinct from tampering —
   verify needs a way to tell the two apart. This requirement only makes sense if
   archiving actually does something to a record that would otherwise look like
   tampering to the normal chain walk — a passive status flag on an untouched row
   would give verify nothing to false-positive on. Signals archiving needs to reach
   deeper than a flag.

3. **Structured redaction** — fields within a record's `payload` may contain
   sensitive data (account numbers, personal identifiers) that must be redactable for
   privacy, without breaking the hash chain.
   *Intent:* satisfy data privacy requirements (right-to-erasure / data minimization
   for specific fields) while preserving tamper-evidence for the rest of the record and
   the chain overall.

4. **Redaction must not invalidate the hash** — explicitly flagged by the source
   document as "a genuine engineering problem." The original hash covers the original
   value; naive removal invalidates it. Design and implement a scheme satisfying both
   tamper-evidence and privacy; document trade-offs and limitations of the chosen
   approach.
   *Intent:* the document is explicitly signaling this is the crux of Scenario B —
   demonstrated engineering reasoning is expected here, not just an implementation.

5. **Bulk export** — endpoint to export all records for a given `resourceId` or
   `actorId` as a self-contained, verifiable bundle; the bundle must include enough
   chain metadata for a recipient to independently verify the included records haven't
   been altered since export.
   *Intent:* let a third party with no access to the live database independently
   verify an extracted subset's integrity — connects directly to the export nuance
   already flagged and accepted as a trade-off under 7a (single global chain means
   exported records are a non-contiguous slice of it).

### Ambiguities & Decisions

Grouped by dependency, same approach as Scenario A. The raw-requirements pass already
surfaced a load-bearing dependency: req 2's "verify must not false-positive on
archived records" only makes sense if archiving does something to a record that would
otherwise look like tampering — which means retention likely reuses whatever mechanism
redaction establishes for removing/relocating content without invalidating hashes.
Redaction is worked first as the foundational mechanism.

#### Group 1 — Redaction mechanism (foundational)

##### 3a — Core mechanism: per-field salted-hash commitments
- **Status:** ✅ Decided
- **Decision:** `payload` is hashed as a **per-field salted commitment structure**,
  not a single flat blob. For each top-level field: `fieldHash(key) = SHA256(salt ||
  canonical(key) || canonical(value))`, salt fresh-random per field, stored alongside
  the hash (not secret — defeats brute-force, doesn't need to be hidden).
  `payloadCommitment = SHA256(canonical(sorted (fieldName, fieldHash) pairs))`
  substitutes for the raw payload value as `contentHash`'s eighth input (6b/6c
  otherwise unchanged). To redact a field: overwrite its raw stored value; retain its
  `(hash, salt)` forever, untouched. At verify: recompute `fieldHash` fresh for
  present fields (proves untouched fields weren't altered); use the retained hash
  directly for redacted fields; feed the reconstructed `payloadCommitment` into the
  normal `contentHash` recomputation and compare against the permanently-stored value.
- **Rationale — why not a single whole-payload hash+salt:** a flat hash over
  concatenated data is a mathematically all-or-nothing commitment — there's no way to
  prove a subset of the original input is unchanged without possessing the complete
  original (that selective-disclosure property doesn't exist for a plain hash; it's
  exactly what distinguishes a Merkle-style commitment scheme from one). Practically:
  with a whole-blob hash, the instant *any* field is redacted, the retained hash can
  never again be recomputed from anything, so verification is lost not just for the
  redacted field but for *every other field in that payload too*. Concrete failure
  this would open: a `RECORD_UPDATED` event with `payload = {accountNumber,
  previousValue, newValue, updatedBy}` where `accountNumber` is legitimately redacted
  — under whole-blob hashing, that one legitimate redaction also silently disables
  verification of `previousValue`/`newValue`/`updatedBy`, letting an attacker (or
  someone exceeding their redaction authorization) tamper with those fields
  undetected, at the same time or any time after. Per-field commitments catch that
  immediately, since each field's hash is independently checkable.
  - **Complexity note:** not meaningfully more expensive than a single hash+salt —
    same primitive (salted SHA-256), applied per top-level field instead of once over
    the concatenated blob, plus a small map instead of a single pair.
  - **Security note — salting is load-bearing, not optional:** low-entropy sensitive
    values (a 9-digit SSN has only `10^9` possibilities) are trivially brute-forceable
    from an unsalted hash via dictionary attack — that's the appearance of redaction,
    not actual privacy, for exactly the kind of data (account numbers, personal
    identifiers) the requirement calls out. Salting forces brute-force cost to be paid
    per field rather than precomputed once.
  - **Amends the already-locked Scenario A decision 6c** (payload was previously
    hashed as one flat canonical blob) — cross-referenced there rather than
    duplicated, to avoid the two copies drifting.

##### 3c — Reversible or permanent?
- **Status:** ✅ Decided
- **Decision:** Irreversible by design. Once the raw value is overwritten, only its
  one-way salted hash remains — no path back.
- **Rationale:** Correct semantic match for "redaction for data privacy" — a
  reversible redaction is a display toggle, not erasure, and wouldn't satisfy genuine
  privacy requirements (GDPR-style right-to-erasure semantics). Crypto-shredding
  (encrypt, then destroy the key) was the alternative mechanism that would have
  offered reversibility, but wasn't needed here and would have required pre-encrypting
  designated fields at write time plus real key-management infrastructure — not
  justified without a reversibility requirement driving it.

##### 3b — Trigger model
- **Status:** ✅ Decided
- **Decision:** Operator-facing endpoint (e.g. `POST
  /audit/events/{sequence_number}/redact` naming a field path) — not
  automatic/policy-driven.
- **Rationale:** Matches the text ("must be redactable," a capability, not a
  schedule) and matches how real redaction requests actually arise (a data-subject
  erasure request, a legal order) rather than a predictable age-based trigger.
  Redaction is itself recorded as a new system event appended to the same chain
  (documenting which record/field, who, when) — becomes the authorization trail 3f
  needs, turning "this record looks incomplete" into something verifiable rather than
  a red flag.

##### 3d — Granularity
- **Status:** ✅ Decided
- **Decision:** Top-level `payload` fields only (not arbitrary nested paths), and
  single-record only (not bulk/cross-record redaction).
- **Rationale:** The mechanism generalizes to full recursive Merkle-tree commitments
  over arbitrarily nested JSON, but that's meaningfully more implementation surface
  (recursive tree construction/verification, path-addressing syntax) with nothing in
  the requirement asking for it. A flat per-field map keeps the commitment structure
  simple while still solving the stated problem correctly — a deliberate, documented
  scope boundary: sensitive data nested inside a sub-object should be structured as
  its own top-level field, or the sub-object redacted wholesale. Single-record scope
  matches the text literally ("fields within *a record's* payload").

#### Group 2 — Redaction surfacing (depends on Group 1)

##### 3e — Query API representation of a redacted field
- **Status:** ✅ Decided
- **Decision:** A structured marker replaces the raw value at redaction time:
  `{"__redacted__": true, "redactedAt": "<ISO8601>", "redactionEventSeq": <seq>}`,
  where `redactionEventSeq` points at the `FIELD_REDACTED` system event (3b) that
  authorized it. The Query API needs no redaction-aware branching — the marker *is*
  the stored value once redaction happens, served like any other field.
- **Rationale:** A bare `null` was the simpler alternative but erases the distinction
  between "never had a value" and "value was deliberately removed" — exactly the kind
  of information an audit log exists to preserve. Embedding `redactionEventSeq`
  directly in the marker means anyone looking at the record can trace who/when/why
  without knowing to look elsewhere.

##### 3f — Verify's reporting of legitimately redacted records
- **Status:** ✅ Decided
- **Decision:** No special case needed — a record with legitimately redacted fields
  verifies identically to a fully-intact one.
- **Rationale:** Direct consequence of 3a's design: verify recomputes `contentHash`
  using the retained per-field hash for redacted fields and a fresh hash for present
  ones, so there's no separate code path and nothing that could produce a false
  positive. Contrasts with req 2 (retention), which *explicitly* asks verify to handle
  archived records without false positives — no equivalent explicit ask exists for
  redaction, only that the scheme "satisfies tamper-evidence," already guaranteed by
  3a's mechanism. Discoverability of which records have redactions is served by 3b's
  `FIELD_REDACTED` events via the ordinary Query API, not by verify's response —
  keeps verify's job narrow (intact vs. broken, and where) rather than becoming a
  general audit-analytics endpoint.

#### Group 3 — Retention (depends on Group 1's mechanism)

##### 1d — Does archiving reduce/relocate data, or only change lifecycle status?
- **Status:** ✅ Decided — the central fork the rest of Group 3 depends on.
- **Decision:** Reduces data. Same textual logic as the raw-requirements pass: req 2's
  "verify must not false-positive on archived records" only makes sense if archiving
  does something that would otherwise look like tampering — a passive status flag on
  an untouched row gives verify nothing to false-positive on.

##### 1a — Archivable vs. soft-deletable: two mechanisms, or one?
- **Status:** ✅ Decided
- **Decision:** One mechanism, not two features. Given 1d, "archived" effectively *is*
  a soft-delete of content — the document offers two names for the same underlying
  capability, not two things to build separately.

##### Mechanism (covers 1a/1d together)
- **Decision:** **Not** a full-record reuse of 3a's per-field commitment structure.
  Per-field commitments exist to enable *selective* disclosure — proving some fields
  unchanged while another is hidden. Full-record archival doesn't need that; nothing
  is being selectively proven, the whole record's content goes away at once. Simpler
  design: **null out the detail fields** (`eventType`, `actorId`, `resourceType`,
  `resourceId`, `payload`, `timestamp`, `recordedAt`), set `archived=true` +
  `archivedAt`, while `sequence_number`, `contentHash`, `prevHash` remain forever
  untouched — those three are never affected by anything, ever.
- Each sweep appends a **`RECORD_ARCHIVED` system event** to the chain (mirrors 3b's
  `FIELD_REDACTED` event design), so archival actions are themselves part of the
  discoverable, tamper-evident history.

##### 2a — What does archiving do to a record's stored representation that verify would otherwise flag?
- **Status:** ✅ Decided
- **Decision:** Nulls the detail fields listed above (per the Mechanism entry),
  leaving verify's standard content-recompute step with missing inputs.

##### 2b — Does verify need new logic to validate a "tombstone" record?
- **Status:** ✅ Decided
- **Decision:** Yes — one explicit new branch: if `archived=true`, skip
  content-recompute for that record (trust the permanently-stored `contentHash`
  directly) and perform the **link check normally**, unchanged. The retained
  `contentHash` stays protected from undetected tampering via 6d's `recordHash`
  cascade — altering it would break the next record's link exactly as it would for
  any other record.
- **Rationale:** Without this branch, verify would recompute `contentHash` from
  now-null fields and produce a spurious `CONTENT_MISMATCH` — exactly the false
  positive req 2 warns against.

##### 2c — Does an archived record still occupy its `sequence_number` slot for gap-detection purposes?
- **Status:** ✅ Decided
- **Decision:** Yes, unaffected. Archiving never removes a row, only nulls its
  content — the row and its `sequence_number` stay in the primary table forever, so
  gap detection has nothing to trip on.

##### 1b — Configurable window: global setting vs. per-policy-rule
- **Status:** ✅ Decided
- **Decision:** Single global setting (e.g. `RETENTION_WINDOW_DAYS`), not per-
  `eventType`/`resourceType` policy. Consistent with 5c's page-size precedent —
  tunable constant, no requirement signal for finer granularity.

##### 1c — What triggers archival: scheduled sweep, on-demand endpoint, or lazy/computed-at-query-time status?
- **Status:** ✅ Decided
- **Decision:** Admin-triggered sweep endpoint (e.g. `POST /audit/retention/sweep`),
  not an in-process scheduler.
- **Rationale:** Building a task-scheduling subsystem (Celery/APScheduler) is
  disproportionate to what's asked. An external cron hitting this endpoint is the
  standard, simpler operational pattern — scheduling is a deployment concern, not
  something the service needs to own internally.

##### 1e — Do archived records stay in default Query API results?
- **Status:** ✅ Decided
- **Decision:** Excluded from default results, opt-in via a flag (e.g.
  `includeArchived=true`).
- **Documented limitation:** once archived, a record's full original content is
  **not retrievable** through this service — mirrors redaction's irreversibility, at
  record granularity. A cold-storage tier preserving full content for later retrieval
  is a reasonable extension, explicitly scoped out given the time budget.

##### DB privilege — closes Scenario A's 2a forward-looking note
- **Decision:** Both redaction (overwriting a payload field) and archival (nulling
  detail columns) require `UPDATE` access the base `app_role` (SELECT/INSERT only)
  doesn't have. Both share one separate, narrowly-scoped role using Postgres
  column-level grants — `UPDATE` on exactly the payload/detail columns these two
  operations touch, and **never** on `sequence_number`, `contentHash`, or `prevHash`,
  which stay permanently un-updatable by anything, including this elevated role.

#### Group 4 — Bulk export (depends on Groups 1–3)

##### 5a — What "verifiable" means given 7a's single global chain: per-record self-consistency vs. inclusion proof
- **Status:** ✅ Decided
- **Decision:** Per-record content self-consistency (recompute each record's
  `contentHash` from its own included fields, per 3a's mechanism) **plus a
  bundle-level digital signature** — not a Merkle-style inclusion proof anchored to
  the live chain.
- **Rationale:** Self-consistency alone is weak — if a record's content and its
  accompanying hash are both plain fields in the same mutable file, an attacker
  editing the bundle can edit both together, consistently, defeating the check
  entirely (same problem as a checksum shipped in the same download it verifies). A
  bundle-level signature is what actually delivers "not been altered since export":
  an attacker without the private signing key can edit the bundle but can't produce a
  signature that still validates.
- **Alternative considered and rejected — constructing a second, export-scoped chain
  over just the filtered records:** would let a recipient run the same walk-and-verify
  algorithm against the bundle as `/audit/verify` runs live, but adds no real security
  once the bundle is already signed. Content-tampering detection is identical either
  way (content hashes are unchanged). What a second chain adds is a "no gaps among the
  *included* records" claim — but the only entity capable of omitting a record from
  the export is the exporting service itself, at export time, which is the same
  entity that signs the bundle; a compromised/dishonest signer can produce a
  perfectly self-consistent rechained bundle missing a record just as easily as a
  flat one. Post-export file tampering (reordering, dropping a record) is already
  caught by the bundle-level signature invalidating on any edit — a second chain
  construct duplicates that protection rather than adding to it.
- **Real risk identified, not cryptographic — trust calibration:** a rechained bundle
  that a recipient can "verify" with a familiar chain-walk is easy to over-read as
  "proven complete and authoritative," when the actual guarantee is narrower: these
  specific records are self-consistent and the signer vouches for them. Rechaining
  doesn't strengthen the underlying guarantee, it just makes it *look* stronger —
  a genuine hazard in a compliance context where over-reading matters. Documented
  explicitly rather than left implicit: the bundle's "verified" status means
  self-consistent + signed as of export time, **not** proven complete relative to the
  live system. Completeness/authenticity ultimately still rests on trusting the
  signer either way — same fundamental limitation already accepted for the live
  chain's lack of external anchoring (6d/8a).
- **Where a stronger design would go, if ever needed:** anchor the export's genesis to
  the actual `recordHash` of the record immediately preceding the first exported one
  in the real global chain (an inclusion-proof-style design), rather than an
  arbitrary export-local constant — ties the bundle back to the live chain's real
  structure. Meaningfully more implementation surface (proof material per record
  relative to its *actual* neighbors, not just its exported ones); nothing in the
  requirement asks for this strength, so deliberately scoped out.

##### 5b — Does the bundle carry a chain-tail anchor/snapshot at export time?
- **Status:** ✅ Decided
- **Decision:** Yes — `chainTailSnapshot` (`sequenceNumber` + `recordHash` of the
  global chain's tail at export time), included as part of the *signed* content.
- **Rationale:** Not required to satisfy the literal requirement (the signature
  already does), but cheap to include and gives forward value: if the signing key
  were ever compromised, an independently-published external checkpoint of chain-tail
  state (future work, per 6d/8a) would be the only thing that could still catch a
  forged bundle, and this snapshot is what a recipient would cross-check against it.

##### 5c — Bundle format and whether bundle-level integrity (signing) is needed
- **Status:** ✅ Decided
- **Decision:** JSON, returned by the export endpoint, structure:
  ```json
  {
    "exportedAt": "<ISO8601>",
    "filter": {"resourceId": "..."},
    "chainTailSnapshot": {"sequenceNumber": N, "recordHash": "..."},
    "records": [{"...fields...": "...", "contentHash": "...", "prevHash": "...", "sequenceNumber": N}],
    "signingKeyId": "...",
    "signature": "<Ed25519 signature over the canonical serialization of everything above>"
  }
  ```
  `records` sorted by original `sequence_number` — cheap, gives a recipient an
  informal at-a-glance sense of gaps, and is already covered by the signature (so
  reordering after export is still caught) without inventing a second chain
  construct. Signed with **Ed25519** (asymmetric — the service holds the private
  signing key, the public key is distributed/documented for recipients to verify
  against). Asymmetric, not HMAC/symmetric, because the recipient is a genuine
  external third party (regulator, auditor) — a symmetric scheme would let anyone
  capable of verifying also forge new bundles. `signingKeyId` supports future key
  rotation without invalidating older exports.
- **Operational note, not solved further here:** the private signing key needs secure
  generation/storage (env-var/secrets-manager-loaded at startup, never committed) — a
  proper KMS/HSM is the production answer; an env-var-loaded key is the proportional
  choice for this prototype, documented as such rather than silently simplified.

##### 5d — How does export represent a record with redacted fields?
- **Status:** ✅ Decided
- **Decision:** No special handling — falls out of 3a/3e for free. Export serializes
  the record's current state, redaction markers included, and per-record hash
  verification works identically to the live system since it already uses retained
  per-field hashes for redacted fields.

##### 5e — Can archived records be exported?
- **Status:** ✅ Decided
- **Decision:** Yes. Follows 2b's rule — an exported archived record is marked
  `archived`/`archivedAt`, and its verification trusts the stored `contentHash`
  directly (skipping recompute from now-null fields), exactly as live verify does.
  Ultimately backstopped by the bundle-level signature either way.
- **⚠️ Known limitation, found during B3 implementation (live testing), not a design
  gap silently missed:** in practice this is close to unreachable through the
  export endpoint's filters. Archival nulls every column export can filter on
  (`resourceType`, `resourceId`, `actorId`, `eventType`, `timestamp`) — once a
  record is archived, no filter value can match it anymore, since it's comparing
  against `NULL`. This is the same consequence already accepted for the query
  endpoint's `includeArchived` flag (1e), but is more consequential here: a
  regulator/compliance officer's most natural export request — "show me the
  complete history for this account, including anything since archived" — can't be
  satisfied by this endpoint once the relevant records are archived, since there's
  no longer an attribute value to filter by. Reaching an archived record via export
  requires already knowing its `sequence_number` from before it was archived (not
  currently an export parameter). Explicitly chosen not to fix now: extending
  export with `sequence_number`-range filters (which survive archival) is a
  reasonable additive fix, and reopening what archival nulls (Scenario B 1d/2a/2b)
  to preserve classification fields is a larger, more invasive alternative — both
  deferred rather than silently patched over. See also Scenario C's clarified
  requirement, which inherits this limitation directly.

### Next steps

Scenario B's ambiguity list is fully resolved — every item across all four groups is
Decided. Next: Scenario C's requirement clarification (intentionally under-specified —
different process than A/B, since there's no source text to extract ambiguities
against, only a clarified requirement statement to construct).

---

## Scenario C — Compliance Reporting

Different shape from A and B — no numbered spec to extract raw requirements from,
just one intentionally under-specified product statement. The deliverable here is the
clarification process itself, not a resolved ambiguity list against existing text.

### Original (Ambiguous) Requirement

> "Regulators need to be able to audit access to client account data."

### Ambiguities Identified

##### C1 — Who are "regulators," and how do they interact with the system?
- **Status:** ✅ Decided
- **Decision:** Internal compliance/audit staff generate reports *for* regulators
  (e.g. during an examination) — regulators never authenticate to or query this
  system directly.
- **Rationale:** The far more common real-world pattern (compliance teams produce and
  hand over records rather than granting external parties logins to internal
  systems), and it keeps the scenario focused on what it's actually testing —
  requirement clarification and reporting design — rather than pulling in a
  regulator-facing identity/access-management build. This was the one genuine fork
  worth confirming before committing, given how much scope it determines; user
  confirmed this framing.

##### C2 — What does "access" mean?
- **Status:** ✅ Decided
- **Decision:** Read/view events specifically, not any interaction.
- **Rationale:** The sharper, more distinctly regulatory concern (unauthorized
  viewing, insider-trading-adjacent monitoring) — the aspect a write-focused audit
  log would otherwise under-capture. Write events remain fully covered by Scenario A
  regardless of this framing.

##### C3 — What counts as "client account data"?
- **Status:** ✅ Decided, with a documented limitation
- **Decision:** Whatever `resourceType`(s) the caller specifies when running a
  report — not a hardcoded taxonomy baked into this system. Consistent with 1a's
  decision to leave `resourceType` free-form rather than a fixed enum.
- **Documented limitation (user-identified):** client account data may also appear
  embedded within the `payload` of records whose `resourceType` isn't itself an
  account-type (e.g. a support-ticket or case-management record that references an
  account number in passing). Such records would not be surfaced by
  `resourceType`-based filtering alone. **Partial, already-existing mitigation:**
  because filtering isn't restricted to `resourceType` — `actorId` and time-range
  filters are independent (per Scenario A's Query API, req 4) and bulk export filters
  by `resourceId` *or* `actorId` (Scenario B) — compliance staff can search across
  *any* `resourceType` by actor or time window without needing to know the exact
  `resourceType` label used for a given interaction. **Not mitigated:** true
  full-text/payload-content search (e.g. "find all records mentioning account number
  X regardless of resourceType") would require a different capability — indexed
  payload search — and is explicitly out of scope for this prototype. Doesn't change
  the design; documented so the limitation is explicit rather than silently assumed
  away.

##### C4 — What's the actual deliverable — a query, or a report?
- **Status:** ✅ Decided
- **Decision:** Scenario B's signed bulk export, filtered to access-type events for
  specified account(s)/actor(s)/time range — not a new reporting subsystem built from
  scratch.
- **Rationale:** A "compliance report for regulators" maps directly onto a
  self-contained, independently-verifiable bundle — exactly what Scenario B already
  provides. Building Scenario C as an application of A/B's existing capabilities,
  plus a thin purpose-built layer on top, is a meaningfully smaller and more coherent
  build than a parallel reporting pipeline, and reuses infrastructure that's already
  been designed for exactly this trust profile (tamper-evident, third-party
  verifiable).

##### C5 — Regulatory-framework specificity
- **Status:** ✅ Decided
- **Decision:** Not targeting compliance with any named regulation (SEC 17a-4,
  FINRA, GDPR, SOX, etc.) — a general capability that would plausibly *support* such
  obligations, not certified compliance with one.
- **Rationale:** Researching and implementing a specific securities-law recordkeeping
  requirement is a legal-research undertaking disproportionate to this exercise; the
  document doesn't name one, and inventing that scope unprompted would be assuming
  facts not in evidence.

##### C6 — Auth/authz for who may run reports
- **Status:** ✅ Decided
- **Decision:** Out of scope, documented as an explicit assumption rather than
  silently dropped.
- **Rationale:** Follows from C1 — flagged back in Scenario A's 2a decision as a
  question to revisit if Scenario C's regulator framing pulled auth/authz back into
  scope; given C1's resolution (internal staff, not external regulator logins), it
  doesn't.
- **⚠️ REVISED (post-review, see "Requirement Change — Authentication &
  Authorization" below):** raised during external review of the initial submission
  — the prototype had no caller authentication on *any* endpoint, not just export.
  C1's "regulators never authenticate directly" resolved who runs compliance
  reports; it never addressed whether the system should authenticate its callers at
  all, and conflating the two was the actual gap. This decision is superseded by
  C7–C11.

### Clarified Requirement Statement

> Internal compliance/audit staff must be able to generate a complete,
> independently-verifiable record of read/view access to client account data —
> scoped to a specified account (`resourceType`/`resourceId`), actor, and/or time
> range — suitable for production to external regulators during an examination. This
> reuses the existing tamper-evident audit log (Scenario A) and signed bulk-export
> mechanism (Scenario B) rather than introducing a parallel reporting pipeline.
>
> **Explicit scope boundaries:**
> - Authentication/authorization for who may run reports is out of scope (C6).
> - Instrumentation of every application read-path to emit access events is out of
>   scope — this system reports on access events that *are* captured; it doesn't own
>   guaranteeing every read path logs one.
> - "Client account data" is scoped by caller-specified `resourceType`(s) — not a
>   hardcoded taxonomy (C3).
> - Full-text/payload-content search is out of scope (C3) — client account data
>   referenced inside payloads of non-account-`resourceType` records won't be
>   surfaced by this design; partially mitigated by independent `actorId`/time-range
>   filtering, not fully solved.
> - Exported reports cannot reach *archived* records by account/actor once their
>   classification fields are nulled by retention (found during B3 implementation —
>   see Scenario B, item 5e). A regulator asking for "the complete history,
>   including anything since archived" cannot be fully satisfied by this endpoint;
>   reaching an archived record requires already knowing its `sequenceNumber` from
>   before archival.
> - **⚠️ REVISED — no longer a scope boundary; see below.** The API now requires
>   caller authentication and role-based authorization on every endpoint except
>   `GET /health` and `GET /audit/export/public-key` (C7–C9), and `compliance`/
>   `reader` principals can be scoped to specific accounts, with cross-account
>   access denied (C12).

### Requirement Change — Authentication & Authorization (post-review)

Raised during external review of the initial submission, after Scenarios A–C were
otherwise complete: the prototype authenticated *nothing* — any caller could write
events, redact fields, sweep retention, or pull a compliance export, all under any
claimed `actorId`. Filed here, reopening C6, rather than as a new scenario, since it's
the same question C6 already asked and answered too narrowly — C1 resolved *who
consumes* a compliance report (regulators, indirectly, via internal staff); it never
asked *who may call the API at all*. That's the actual gap.

##### C7 — Authentication mechanism
- **Status:** ✅ Decided
- **Decision:** Static API keys, presented via a request header (`X-API-Key`),
  checked against a config-loaded map of `key → {principalId, roles}`.
- **Rationale:** Simplest mechanism that still enforces a real boundary, and
  consistent with the project's existing dev-fixed-secrets pattern (export signing
  key seed, DB role passwords — see C11). Confirmed with the user before proceeding,
  given how much downstream design hangs off this choice.
- **Rejected alternatives:**
  - **JWT bearer tokens** — more "industry standard" (built-in expiry, richer
    claims), and the `cryptography` dependency already used for export signing would
    make issuing them easy. Rejected because it requires either standing up a token
    issuer or minting long-lived test tokens to stand in for one — meaningfully more
    moving parts than a 2–3 day prototype needs to demonstrate the enforcement
    boundary itself.
  - **mTLS (client certificates)** — strong service-to-service identity, but
    requires a local CA, cert issuance/rotation tooling, and TLS-termination changes
    to the dev stack. Disproportionate infrastructure for what this exercise needs
    to show.

##### C8 — Coverage: system-wide, or just Scenario C's endpoints?
- **Status:** ✅ Decided
- **Decision:** System-wide. Every endpoint requires a valid API key **except**:
  - `GET /health` — a liveness probe; orchestrators/load balancers don't carry
    application credentials.
  - `GET /audit/export/public-key` — must stay fetchable by parties who never hold
    credentials in this system at all, per C1: an external regulator receiving a
    bundle from internal compliance staff needs to verify its signature without an
    account here. Gating the public key behind auth would defeat the
    self-contained, independently-verifiable bundle design Scenario B already
    committed to.
- **Rationale:** Filing this under Scenario C doesn't mean scoping enforcement to
  just its endpoints — an authenticated `/audit/export` sitting next to a wide-open
  `POST /audit/events` wouldn't cohere as "this service has auth," and the review
  feedback that prompted this was about the system's callers generally, not
  compliance reporting specifically.

##### C9 — Role model
- **Status:** ✅ Decided
- **Decision:** Four flat roles (no hierarchy — a principal needing multiple
  capabilities holds multiple roles), mapped to the actor conventions already used
  informally throughout the docs (`compliance-officer-1`, `cron-scheduler`) rather
  than inventing a new taxonomy:

  | Role | Endpoints |
  |---|---|
  | `writer` | `POST /audit/events` |
  | `reader` | `GET /audit/events`, `GET /audit/verify` |
  | `compliance` | `GET /audit/export`, `POST /audit/events/{sequence_number}/redact` |
  | `scheduler` | `POST /audit/retention/sweep` |

- **Rationale:** `export` sits under `compliance`, not `reader` — C1 already
  established export's purpose as compliance staff producing regulator-facing
  reports, not general read access, so gating it at `reader` would under-restrict
  it relative to what the requirement itself says it's for.
- **Relationship to the existing DB-level roles (2a):** additive, not a
  replacement. API-level roles gate *which caller may invoke which endpoint*;
  `app_role`/`maintenance_role` remain the independent second layer limiting *what
  the app's own DB connection may do regardless of caller* — defense-in-depth
  against a compromised app process, not just an unauthenticated one. There's a
  rough alignment for legibility (`writer`/`reader` endpoints run under
  `app_role`; `compliance`/`scheduler` endpoints run under `maintenance_role`) but
  it's not a hard 1:1 coupling enforced in code.

##### C10 — Does authenticating callers change how `actorId` is trusted?
- **Status:** ✅ Decided
- **Decision:** For `POST /audit/events/{sequence_number}/redact` and
  `POST /audit/retention/sweep`, `actorId` is no longer a caller-supplied request
  field — it's derived server-side from the authenticated principal. For
  `POST /audit/events`, `actorId` stays caller-supplied, unchanged.
- **Rationale:** Redact/retention's `actorId` represents a claim about *who is
  performing this administrative action* — previously spoofable by anyone, since
  there was no authenticated identity to check it against. Once callers are
  authenticated, continuing to trust a self-asserted identity for a self-auditing
  action (both operations append their own audit event) would defeat the point of
  adding auth at all. Write's `actorId` means something structurally different —
  it's the *subject of the recorded domain event* (e.g. who logged in), not a claim
  about the calling principal; a producer service legitimately writes events on
  behalf of many different `actorId`s, so it correctly stays caller input.
- **Consequence:** `RedactRequest`/`RetentionSweepRequest` lose their `actorId`
  body field — a breaking change to those two request shapes, to be reflected in
  `TASKS.md`, the test suite, and the README's "Using the API" examples.

##### C11 — Secrets handling for API keys
- **Status:** ✅ Decided
- **Decision:** Dev-fixed keys defined in `Settings`/config, same pattern already
  used for the export signing key seed and DB role passwords.
- **Rationale:** Filed under the same already-documented "secrets externalization"
  limitation (`TASKS.md`'s "Production-readiness extensions," `TESTING.md`'s "what
  isn't automated") rather than solved now — a real deployment would source these
  from a secrets manager or delegate to an actual IdP, disproportionate to build for
  this exercise.

##### C12 — Principal-to-resource scoping (cross-tenant/cross-account denial)
- **Status:** ✅ Decided
- **Decision:** An API key principal (C7) may carry an optional
  `resourceScope: list[str] | None` — an allow-list of `resourceId` values. `None`
  means unscoped (sees everything; used for e.g. the demo/dev key). When set, it is
  enforced **server-side**, not merely checked against caller-supplied filter
  parameters — the service layer intersects the caller's scope into the query
  itself, so a scoped caller can't see other accounts just by omitting a
  `resourceId` filter. A request naming an out-of-scope `resourceId` explicitly is
  denied with **`404`, not `403`** — a scoped principal shouldn't be able to
  distinguish "that account doesn't exist" from "that account isn't yours."
  Applies to `reader`'s `GET /audit/events` and `compliance`'s `GET /audit/export`
  only. `writer`, `scheduler`, and `GET /audit/verify` are explicitly **not**
  scoped — see rationale.
- **Rationale:** This is the mechanism a "cross-tenant denial test" actually
  exercises — without it, any `reader`/`compliance` key sees every account in the
  log, and there is nothing for such a test to deny. Scoped to `resourceId`
  specifically (not `actorId`) because C3 already established `resourceType`/
  `resourceId` as the "account" analog in this system's data model
  ("client account data" = whatever `resourceType`(s) the caller specifies);
  `actorId` identifies *who acted*, a cross-cutting concern spanning accounts, not
  a unit of data ownership.
  - **Why not `writer`:** a write's `resourceId` describes the *subject of the
    domain event*, not a claim about the calling principal (same reasoning as
    C10) — a single ingestion service legitimately writes events about many
    accounts. The threat this closes is *read*-side confidentiality of client
    data, not write-path integrity, which the hash chain and role gate already
    cover.
  - **Why not `scheduler`:** retention sweep operates on the whole table by
    `recordedAt` age, not by account — there's no single `resourceId` to scope
    an age-based sweep to.
  - **Why not `GET /audit/verify`:** already flagged before this was written up —
    verify walks the single global chain (7a) and returns no payload content,
    only `sequenceNumber`/`violationType`; scoping it per-account would fight the
    single-chain design for no confidentiality benefit, since it discloses
    nothing account-specific to begin with.
- **Rejected alternative — Postgres row-level security (RLS) keyed on a session
  variable:** more robust/production-grade (enforced at the DB layer, not just the
  service layer — can't be bypassed by a bug in application code), but requires a
  schema/session-wiring change disproportionate to this exercise, and doesn't
  obviously fit the existing `resourceId`-per-event granularity (RLS is typically
  keyed on a coarser `tenant_id` column this schema doesn't have). Left as a named
  production trade-off rather than built now — the service-layer intersection
  gives the same caller-visible guarantee for this prototype's purposes.

### Next steps

Requirement clarification complete for all three scenarios. Technical design and the
full actionable task breakdown (all three scenarios, dependency-ordered) now live in
[`docs/TASKS.md`](TASKS.md) — including Scenario C's design, which turned out to be a
small extension of Scenario B's export filters rather than new infrastructure.

C7–C12 (authentication & authorization) are decided but not yet built: `TASKS.md`
needs a new task group, every existing service/router touched by C8's coverage needs
the auth dependency wired in, `RedactRequest`/`RetentionSweepRequest` need their
`actorId` field removed per C10, `query.py`/`export.py` need the scope-intersection
logic per C12, the test suite needs an authenticated `client` fixture plus negative
(missing/invalid key, wrong role) coverage per endpoint **and** a dedicated
cross-account denial test (two `resourceScope`-restricted keys, each confirmed to
reach only their own account's events/export and denied — `404` — on the other's),
and `ARCHITECTURE.md`/`README.md`/`docs/AI_USAGE_LOG.md` need updating to reflect the
new layer.
