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

---

## 2026-08-21 — Scenario A requirement decomposition begins

**Intent:** Work through Scenario A's raw requirements and identify ambiguities before
writing any implementation code, per the assignment's requirement-understanding
criterion. Process: extract raw requirements + intent as a list, identify ambiguities
per requirement, group ambiguities by dependency (decisions that constrain other
decisions go first), then resolve one at a time.

**AI produced:** Full raw-requirement extraction (9 items) from Scenario A with intent
annotations; an ambiguity list against requirements 1–8 (schema, append-only
enforcement, timestamp source, query semantics, pagination, hash content, chain
linkage, verify semantics); a dependency-ordered regrouping of that list into four
passes (foundational architecture → chain mechanics → query/pagination → hardening).

**Decision:** User accepted the raw-requirement extraction and ambiguity list as
presented, with light steering (requested reordering by dependency after reviewing the
first pass). Structure adopted as the working plan for the rest of Scenario A's design
phase.

**Rationale:** Dependency-ordering avoids settling a downstream question (e.g.
pagination strategy) only to have to revisit it once a foundational one (e.g. chain
scope) changes the shape of the problem — which is exactly what happened here: pagination
(5a) discussion was paused mid-way once chain scope was identified as more foundational
and moved ahead of it.

**Artifact:** Started `docs/scenario-a/REQUIREMENTS.md` as the living record of this
process — raw requirements, the grouped ambiguity list, and a decision entry per item
(status, decision, rationale, accepted trade-offs), so any decision can be traced and
revisited. Will be translated into `docs/scenario-a/TASKS.md` once every ambiguity is
resolved.

---

## 2026-08-21 — Decision: chain scope (single global chain)

**Intent:** Resolve ambiguity 7a — should the hash chain be one global sequence across
all events, or partitioned per resource/actor?

**Proposed by user:** Single global chain, justified by (1) it matches the literal
requirement wording ("the chain," singular) and (2) it avoids the complexity of
managing multiple chains for Scenario B's bulk export of mixed resources/actors.

**AI contribution:** Confirmed and extended the justification — the unparameterized
`GET /audit/verify` endpoint is a second textual signal for a single chain; a global
chain also closes a security gap a per-resource design would have (deleting one
resource's entire chain would go undetected by other chains); and it matches how
tamper-evident logs are conventionally built (single monotonic sequence as source of
truth). Pushed back on part of the user's second justification: a global chain does
*not* straightforwardly simplify bulk export — exported records (filtered by
resourceId/actorId) are a non-contiguous slice of the global sequence, so the export
bundle can prove per-record content integrity but not full linkage integrity from the
subset alone. Flagged this as a trade-off to solve explicitly in Scenario B rather than
an unqualified win.

**Decision:** **FINAL.** Single global chain. User reviewed the added nuance and
confirmed to lock in the decision as amended.

**Rationale:** See `docs/scenario-a/REQUIREMENTS.md`, item 7a, for the full writeup
including accepted trade-offs (bulk-export verification design deferred to Scenario B,
concurrency serialization deferred to 7c, verify-endpoint cost growth deferred to
7e/8b).

---

## 2026-08-21 — Decision: event schema shape (1a–1d)

**Intent:** Resolve ambiguities 1a–1d — `eventType` constraint style, whether all six
event fields are truly mandatory, `payload` size/depth limits, and invalid-input
handling.

**AI produced:** Recommended free-form `eventType` with a format pattern (not a
hard enum/registry, since the service must ingest new event types from arbitrary
producers without a migration); all six fields mandatory (reading "at minimum" as a
floor, not an invitation to optionality, reinforced by every example event having a
natural resource and actor); `payload` constrained to a JSON object with a size cap
(~32KB) and nesting-depth cap (~10), primarily to bound hashing cost and rule out a
pathological-JSON processing vector, and because Scenario B's field-level redaction
requires an object shape; validation at the API boundary before any chain-mutating
logic, so rejected writes can never introduce a sequence gap.

**Decision:** **FINAL.** User reviewed with no pushback and confirmed to lock in as
proposed.

**Rationale:** See `docs/scenario-a/REQUIREMENTS.md`, item "1a–1d — Event schema
shape," for the full writeup per sub-item.

---

## 2026-08-21 — Decision: timestamp source (3a/3b), cascading into 7d and 4a

**Intent:** Resolve 3a/3b — caller-supplied vs. server-assigned `timestamp`, and
whether both should exist.

**AI produced:** Recommended keeping both: `timestamp` (caller-supplied, mandatory,
represents source-system occurrence time — often the only accurate value given
ingestion delay) and a new `recordedAt` (server-assigned, monotonic, never
caller-controlled). Argued a purely caller-supplied value is unacceptable as a basis
for chain order given it's attacker-controllable input, while a purely server-assigned
value throws away information investigators want to query by. Noted this resolves 7d
(chain order = `recordedAt`, immune to caller manipulation) and 4a (public time-range
filter targets caller-supplied `timestamp`, since that's the semantic investigators
actually want — `recordedAt` stays internal, not exposed as a second filter dimension,
keeping scope tight since nothing in the source document asks for it). Flagged the
accepted trade-off explicitly: a caller can still misreport `timestamp`, which is a
data-quality problem the hash chain is not meant to catch (it proves non-tampering
after the fact, not truthfulness of caller-supplied content).

**Decision:** **FINAL.** User reviewed with no pushback ("this all looks good") and
confirmed to lock in 3a/3b, 7d, and 4a together.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario A, items "3a/3b," "7d," and "4a."
This closes out Group 1 (foundational architecture) entirely.

---

## 2026-08-21 — Restructure: single REQUIREMENTS.md instead of per-scenario folders

**Intent:** User feedback: avoid creating a separate folder + `REQUIREMENTS.md` per
scenario (`docs/scenario-a/REQUIREMENTS.md`, etc.) — consolidate into one
`docs/REQUIREMENTS.md` with a section per scenario instead.

**Action:** Moved `docs/scenario-a/REQUIREMENTS.md` content into `docs/REQUIREMENTS.md`
under a `## Scenario A` heading, with placeholder `## Scenario B` / `## Scenario C`
sections for later. Removed the now-empty `docs/scenario-a/` folder. Earlier log
entries above still reference the old `docs/scenario-a/REQUIREMENTS.md` path — left
as-is rather than rewritten, since they're a record of what was true at the time; this
entry documents the move for anyone following an old reference.

**Decision:** **FINAL** — user-directed structural correction, applied as stated, no
alternative proposed since it was a straightforward preference with no competing
trade-off.

---

## 2026-08-21 — Decision: concurrency enforcement for chain appends (7c)

**Intent:** Resolve 7c — how to prevent concurrent writers from both linking to the
same chain tail and producing a forked/invalid chain, now that 7a has committed to a
single global chain.

**AI produced:** Presented two options: (1) `SERIALIZABLE` isolation with optimistic
retry, and (2) explicit serialization of the append critical section via lock, with an
application-computed `sequence_number` instead of a DB sequence object. Recommended
(2), specifically a Postgres advisory transaction lock over `SELECT ... FOR UPDATE` on
the last row (the latter needs special-casing for the empty-table/genesis bootstrap;
the advisory lock doesn't). Rejected option (1) with a concrete reason, not just
convention: a DB-generated sequence burns values on any aborted/retried transaction,
which would leave a gap that's indistinguishable from a gap caused by malicious
deletion — undermining the gap-detection semantics 8a will need later. Proposed a
`UNIQUE` constraint on `sequence_number` as a defense-in-depth backstop independent of
the locking mechanism.

**Decision:** **FINAL.** User reviewed and confirmed to lock in as proposed, no
pushback.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario A, item "7c," for the full writeup
including the accepted throughput trade-off (fully serialized writes — a direct
consequence of 7a already accepted, not a new cost).

---

## 2026-08-21 — Decision: hash algorithm, content definition, canonicalization (6a–6c), genesis value (7b)

**Intent:** Resolve 6a–6c together (they're interdependent), and fold in 7b's literal
value which depends on 6a's output encoding — flagged as a sequencing issue with the
earlier Group 2 ordering (7b was listed before 6a despite depending on it).

**AI produced:**
- **6a:** SHA-256, hex-string output. Explicitly ruled out MD5/SHA-1 (practical
  collision attacks) and BLAKE3 (no performance need to justify deviating from the more
  recognized standard). Hex over raw bytes reasoned from JSON having no native binary
  type.
- **6b:** Content hash covers all persisted fields except the hash fields themselves —
  including server-assigned `sequence_number`/`recordedAt`, not just the caller's six
  fields — reasoned from requirement 9's direct-datastore-mutation validation test
  (those server-assigned fields are just as tamperable via direct DB access as the
  caller fields, so must be equally protected). `prevHash` deliberately excluded from
  content hash, kept as a separate field, to make content-tampering and link-tampering
  distinguishable for 8a.
- **6c:** Canonicalize the parsed Python value (sorted keys, fixed separators, UTF-8),
  not raw request bytes — reasoned from a genuine risk that Postgres JSONB doesn't
  guarantee byte-for-byte round-tripping of submitted JSON text, which could otherwise
  produce false-positive tamper detections from cosmetic storage reformatting. Flagged
  numeric int/float JSONB round-trip fidelity as a specific test to write, not an
  assumption to make silently.
- **7b:** `"0" * 64` genesis sentinel, following directly from 6a's hex encoding.

**Decision:** **FINAL.** User reviewed with no pushback and confirmed to lock in all
four together.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario A, items "6a–6c" and "7b," for the
full writeup.

---

## 2026-08-21 — Decision: combined recordHash (6d) and violation taxonomy (8a), revised after user correction

**Intent:** Resolve 6d (does the chain need a combined block hash, or are independent
`contentHash`/`prevHash` fields sufficient?) and 8a (verify's violation taxonomy),
explicitly paired since they're interdependent.

**AI produced (round 1):** Identified that independent `contentHash`/`prevHash` fields
(as scoped by 6b/6c) fail to satisfy req 7's literal cascading-invalidation guarantee —
a two-record forgery (edit N-1, patch N's `prevHash` to match) leaves everything
downstream valid, since `contentHash(N)` never depended on `prevHash(N)`. Proposed a
derived, unstored `recordHash(N) = SHA256(contentHash(N) || prevHash(N))` as the value
the next record's `prevHash` actually points to, so `prevHash(N)` becomes an input to
`recordHash(N)` and tampering cascades correctly. Proposed a four-category violation
taxonomy for 8a, including an independent `SEQUENCE_GAP` check for detecting deleted
records.

**User pushback:** Questioned whether `SEQUENCE_GAP` was ever informative independent
of `LINK_MISMATCH` — reasoned a gap should already show up as a link mismatch.

**AI response:** Verified the claim by working through the cascade: in the realistic
tampering case (a single direct mutation, matching req 9's validation test literally),
a deleted record produces a `LINK_MISMATCH` on its own once "previous" is resolved
dynamically against currently-existing records. An attacker attempting to "heal" the
resulting gap by also rewriting the neighbor's `prevHash` doesn't evade detection
either, given the `recordHash` cascade from 6d — it only relocates the mismatch further
down the chain. The only way to produce zero link mismatches requires rewriting every
record from the edit point to the current tail, which a gap check wouldn't catch
either. Concluded the user was correct: dropped `SEQUENCE_GAP` as an independent
check, folded gap detection into `LINK_MISMATCH` as a diagnostic sub-classification
(names the missing `sequence_number`(s) when the gap is >1) rather than a peer-level
category. Also surfaced a related, explicitly out-of-scope limitation: no purely
algorithmic check can catch an attacker willing to rewrite an entire consistent
alternate tail (including resequencing) — that requires external anchoring, which
isn't part of this prototype.

**Decision:** **FINAL.** Revised three-category taxonomy
(`GENESIS_MISMATCH`/`CONTENT_MISMATCH`/`LINK_MISMATCH`) plus the `recordHash` design,
locked in after the correction.

**Rationale/example of AI being wrong and corrected:** this is a case where the user's
review caught a real design gap the initial proposal missed (an unnecessary, non-load-
bearing check) — logged in full per the traceability requirement (§4.3) since it's a
concrete generated → user-corrected → revised example, not just an accepted
recommendation. See `docs/REQUIREMENTS.md`, Scenario A, item "6d + 8a," for the full
writeup.

---

## 2026-08-21 — Decision: fail-fast verify, synchronous DB-streamed walk (8b, 7e)

**Intent:** Resolve 8b (does verify report only the first inconsistency, or a full
list?) and 7e (synchronous vs. streaming/chunked chain walk).

**AI produced:** Recommended fail-fast (stop and report at the first inconsistency),
reasoned directly from the requirement's literal wording ("the first inconsistency,"
singular) and from records after a break only being derivatively invalid. Recommended
keeping verify a single synchronous HTTP request rather than an async job/polling
model or incremental/checkpointed verification — reasoned those are real production
patterns but over-engineering relative to this assignment's actual validation flow
(small numbers of events, not production scale) and 2–3 day scope — while still using
a streaming DB cursor (not buffering the full table in memory) as a cheap discipline
regardless of scale. Noted explicitly that fail-fast only shortens the broken-chain
case; an intact chain is unavoidably O(n) either way.

**Decision:** **FINAL.** User confirmed to continue, no pushback.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario A, item "8b + 7e." This closes out
Group 2 (chain mechanics) entirely — every item in Groups 1 and 2 is now Decided.

---

## 2026-08-21 — Decision: Group 3 — pagination, sort order, filter semantics (5a/5d, 5b, 5c, 4b, 4c)

**Intent:** Resolve the remaining query/pagination ambiguities in one pass, given
they're lower-stakes and more independent than the Groups 1–2 architecture decisions.

**AI produced:** Cursor/keyset pagination anchored to `sequence_number` (now available
from 7c as a gapless, strictly increasing key), exposed as a raw value rather than an
opaque token; default `limit=50`/max `limit=500`; AND semantics for combined filters;
`resourceType` allowed as a standalone filter (independent of `resourceId`), justified
by a concrete forward link to Scenario C's "audit access to client account data"
framing. For sort order (5b), presented it as a genuine trade-off rather than a
confident recommendation — chain/insertion order (ascending) vs. investigative-tool
convention and Scenario C fit (descending) — and asked the user to decide.

**User decision:** Descending (newest first), explicitly to match the query use case.

**Decision:** **FINAL** for all five items — 5a/5d, 5c, 4b, 4c accepted as proposed; 5b
per user's explicit choice.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario A, items "5a/5d," "5b," "5c," "4b,"
"4c." This closes out Group 3 entirely — only Group 4 (2a) remains before Scenario A's
ambiguity list is fully resolved.

---

## 2026-08-21 — Decision: DB-level immutability enforcement (2a) — Scenario A ambiguity list complete

**Intent:** Resolve 2a, the last open item — whether to add DB-level immutability
restrictions as defense-in-depth alongside hash-chain detection, given req 9's
validation test seems to require a direct datastore mutation to succeed.

**AI produced:** Resolved the apparent tension by distinguishing the application's own
runtime DB credentials from the privileged connection req 9's test implies. Recommended
a least-privilege `app_role` granted only `SELECT`/`INSERT` on `audit_events` (never
`UPDATE`/`DELETE`/DDL), with migrations under a separate role. Framed this as two
layers of defense for two distinct threats: application-layer bugs/injection are
prevented outright (the app's credentials can't issue the operation), while a
privileged actor with direct database access can't be blocked by a role grant at
all — which is precisely the threat the hash chain exists to *detect* rather than
prevent, and precisely what req 9's test simulates. Flagged a forward-looking,
unresolved question for Scenario B: how redaction/retention interact with this
restriction (separate elevated role vs. an append-only design that avoids `UPDATE`
entirely).

**Decision:** **FINAL.** User confirmed to lock in as proposed, no pushback.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario A, item "2a." **This completes
Scenario A's full ambiguity list** — all four groups, every item, Decided. Next step:
translate `docs/REQUIREMENTS.md` into `docs/TASKS.md`.

---

## 2026-08-21 — Scenario A requirements committed; Scenario B decomposition begins

**Action:** Committed `docs/REQUIREMENTS.md` and the accumulated `docs/AI_USAGE_LOG.md`
entries (commit `bb1cf13`) at the user's request, marking Scenario A's requirement
phase complete. Deferred `docs/TASKS.md` — user directed running the same
requirement-refinement process (raw requirements → ambiguities → dependency-ordered
grouping → resolve one at a time) across Scenario B and then Scenario C first, on the
reasoning that finishing requirement discovery for all three scenarios up front reduces
rework if a Scenario A decision needs revisiting once B/C's requirements are fully
understood.

**AI produced:** Raw-requirement extraction (5 items) with intent annotations for
Scenario B, following the same format used for Scenario A. Added to
`docs/REQUIREMENTS.md` under the Scenario B section. One intent annotation flagged a
textual inference worth carrying into the ambiguity discussion: requirement 2 (verify
must not false-positive on archived records) only makes sense if archiving does
something to a record that would otherwise look like tampering — implying archiving
can't be a purely passive status flag on an untouched row.

**Decision:** User to confirm before proceeding to ambiguity identification, per the
same phased cadence used throughout Scenario A (extract → review → identify
ambiguities → reorder by dependency → resolve).

---

## 2026-08-22 — Scenario B ambiguity list + dependency ordering

**Intent:** Identify ambiguities against Scenario B's five raw requirements and
propose a dependency ordering, same process as Scenario A.

**AI produced:** Full ambiguity list across all five requirements (18 items total:
1a–1e, 2a–2c, 3a–3f, 5a–5e), plus a proposed four-group dependency ordering — unlike
Scenario A, proposed upfront in the same pass rather than as a separate reordering
step, since the raw-requirements pass already surfaced the key dependency directly
from the text: req 2 (verify must not false-positive on archived records) only makes
sense if archiving does something to a record that would otherwise look like
tampering, implying retention likely reuses whatever mechanism redaction establishes.
Groups: (1) redaction mechanism — foundational; (2) redaction surfacing — depends on
1; (3) retention — depends on 1's mechanism; (4) bulk export — depends on 1–3 plus the
already-accepted 7a export-nuance trade-off.

**Decision:** User confirmed the list and ordering, no changes requested. Added to
`docs/REQUIREMENTS.md` under Scenario B. Proceeding to Group 1 (redaction mechanism:
3a, 3c, 3b, 3d).

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario B, "Ambiguities & Decisions," for
the full grouped list.

---

## 2026-08-22 — Decision: redaction mechanism (3a, 3c, 3b, 3d) — amends Scenario A's 6c

**Intent:** Resolve Group 1 — the document's explicitly named "genuine engineering
problem": how to make a payload field's value irretrievable while keeping the record's
hash verifiable.

**AI produced (round 1):** Proposed per-field salted-hash commitments — hash each
top-level `payload` field independently with a random salt, commit to the set via
`payloadCommitment`, substitute that for the raw payload value in `contentHash`.
Explained this requires amending Scenario A's already-locked 6c (which hashed
`payload` as one flat blob). Also proposed: operator-facing redaction endpoint with a
chain-appended audit event (3b); irreversible by design (3c); top-level-field,
single-record granularity (3d).

**User pushback:** Asked directly why a single whole-payload salted hash wasn't
sufficient — i.e. why not just retain one hash+salt for the original payload and
directly edit the stored value.

**AI response:** Gave the rigorous reason rather than an appeal to convention: a flat
hash over concatenated data is a mathematically all-or-nothing commitment — there's no
way to prove a subset of the original input is unchanged without possessing the
complete original (no selective-disclosure property without additional structure,
which is exactly what a Merkle-style commitment provides and a flat hash doesn't).
Concretely: with one whole-payload hash, redacting any single field permanently
destroys the ability to verify every *other* field in that payload too, not just the
redacted one. Walked through a concrete failure case (a `RECORD_UPDATED` event where
legitimate redaction of `accountNumber` would also silently disable detection of
tampering with unrelated fields like `newValue`) to make the consequence concrete
rather than abstract. Also noted the complexity delta between one hash and per-field
hashes is small (same primitive, applied more times) — not a costly trade-off.

**Decision:** **FINAL.** User confirmed the reasoning resolved the concern and to lock
in all four items, and to update 6c to reflect the amendment.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario B, item "3a" (full writeup,
including the salting-is-load-bearing security note), and Scenario A, item "6c" (now
carries a flagged `⚠️ REVISED` note cross-referencing 3a rather than duplicating the
mechanism, to avoid the two copies drifting out of sync). This is the second case in
this project where a user question surfaced a real gap or led to a stronger design
before implementation began — logged in full per the traceability requirement (§4.3).

---

## 2026-08-22 — Decision: redaction surfacing (3e, 3f)

**Intent:** Resolve Group 2 — how a redacted field is represented in Query API
responses (3e), and whether verify needs special handling/reporting for records with
redacted fields (3f).

**AI produced:** Structured redaction marker embedding a pointer back to the
authorizing `FIELD_REDACTED` event (`redactionEventSeq`), rather than a bare `null`,
specifically to preserve the "deliberately removed" vs. "never had a value"
distinction. For 3f, concluded no special verify handling is needed at all — a direct,
useful consequence of how 3a's mechanism was designed (redacted records verify
identically to intact ones), contrasted explicitly with req 2's retention requirement
which *does* explicitly ask for false-positive handling, to show this wasn't a gap but
an actual property of the chosen design.

**Decision:** **FINAL.** User confirmed to lock in both, no pushback.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario B, items "3e" and "3f." Closes out
Group 2 entirely. Next: Group 3 — retention (1a, 1d, 1b, 1c, 1e, 2a, 2b, 2c).

---

## 2026-08-22 — Decision: retention/archival (Group 3) — reuses and simplifies from the redaction mechanism

**Intent:** Resolve Group 3 in full — whether archiving reduces data or just flags
status (1d, the central fork), whether "archivable" and "soft-deletable" are one
mechanism or two (1a), the concrete archival mechanism, verify's handling of archived
records (2a/2b/2c), the retention window's configurability (1b), what triggers
archival (1c), and default query visibility (1e). Also closes the forward-looking DB
privilege note left open at the end of Scenario A's 2a decision.

**AI produced:** Resolved 1d from the same textual signal identified during the raw
requirements pass (req 2's false-positive concern only makes sense if archiving is
disruptive to a naive verify walk), which then resolves 1a (one mechanism, not two).
Considered and explicitly rejected reusing 3a's full per-field commitment structure
for whole-record archival — reasoned that per-field granularity exists specifically
for *selective* disclosure, which full-record archival doesn't need, since nothing is
being selectively proven when an entire record's content is discarded at once.
Proposed a simpler mechanism instead: null the detail fields, keep
`sequence_number`/`contentHash`/`prevHash` permanently untouched. This directly
determines 2a (nulled fields would break verify's content-recompute) and 2b (verify
needs one new branch: skip content-recompute when `archived=true`, trusting the
permanently-stored `contentHash`, still protected indirectly via 6d's `recordHash`
cascade). 2c falls out for free (row never removed, so gap detection is unaffected).
Proposed a `RECORD_ARCHIVED` chain event mirroring 3b's `FIELD_REDACTED` pattern for
consistency. For 1b/1c/1e, applied the same scope discipline as Scenario A (single
global config constant, an admin-triggered endpoint rather than an in-process
scheduler, default-excluded query visibility) and flagged an explicit, honest
limitation: archived records' full original content is not retrievable through this
service once archived — a cold-storage retrieval tier was named as a reasonable
extension but scoped out given time constraints, not silently omitted. Closed
Scenario A's 2a forward-looking note by giving redaction and retention a shared,
narrowly-scoped DB role with column-level `UPDATE` grants, explicitly excluding the
three permanent chain-integrity columns from ever being updatable by anything.

**Decision:** **FINAL.** User confirmed to lock in all of Group 3, no pushback.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario B, items "1d," "1a," "Mechanism,"
"2a," "2b," "2c," "1b," "1c," "1e," and "DB privilege." Closes Group 3 entirely. Next:
Group 4 — bulk export (5a, 5b, 5c, 5d, 5e).

---

## 2026-08-22 — Decision: bulk export (Group 4) — signature over rechaining, user-proposed alternative analyzed and rejected

**Intent:** Resolve Group 4 in full — what "verifiable" means for the export bundle
given 7a's non-contiguous-export trade-off (5a), whether to include a chain-tail
anchor (5b), bundle format and integrity mechanism (5c), and how redacted/archived
records are represented in exports (5d/5e).

**AI produced (round 1):** Proposed per-record self-consistency (recompute each
record's `contentHash`) plus a bundle-level Ed25519 signature — identified that
self-consistency alone is weak, since an attacker able to edit the bundle file can
edit a record's content and its accompanying hash together, consistently, with
nothing to catch it. The signature is what actually delivers "not altered since
export."

**User question:** Asked specifically about an alternative — constructing a *new*,
export-scoped hash chain over just the filtered records (re-linking them into their
own contiguous sequence), signed the same way, and whether that would improve
verifiability or introduce new attack risk.

**AI analysis:** Worked through why rechaining adds no real security once the bundle
is already signed: content-tampering detection is identical either way (content
hashes unchanged); the only new claim rechaining adds is "no gaps among the *included*
records," but the sole entity capable of omitting a record from the export is the
exporting service itself at export time — the same entity that signs the bundle — so
a dishonest/compromised signer can produce a perfectly self-consistent rechained
bundle missing a record just as easily as a flat one. Post-export file tampering is
already fully caught by the signature invalidating on any edit, so a second chain
construct duplicates that protection rather than adding to it. Identified a *different*
kind of risk the user's proposal would introduce — not cryptographic, but
trust-calibration: a rechained bundle a recipient can "verify" with a familiar
chain-walk is easy to over-read as proving completeness/authoritativeness, when the
actual guarantee is narrower (self-consistent + signed by the service). Named the
direction a genuinely stronger design would take (anchor the export's genesis to the
real chain's actual neighboring record, an inclusion-proof-style scheme) and why it's
deliberately out of scope (meaningfully more implementation surface, nothing in the
requirement asking for that strength).

**Decision:** **FINAL.** User's proposed alternative was seriously evaluated, not
dismissed — the analysis is recorded in full in `docs/REQUIREMENTS.md` under 5a rather
than only the conclusion, since it's a legitimate design question worth being able to
re-derive later. Landed on flat per-record hashes + Ed25519 bundle signature +
sequence-sorted record order (cheap, already covered by the signature) + an explicit,
documented statement that "verified" means self-consistent-and-signed, not
proven-complete. 5b (chain-tail snapshot, included as defense-in-depth), 5c (bundle
format/signing scheme), 5d, and 5e locked in alongside it, no further pushback.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario B, items "5a" (includes the full
rechaining analysis), "5b," "5c," "5d," "5e." **This completes Scenario B's full
ambiguity list** — all four groups, every item, Decided. Next: Scenario C.

---

## 2026-08-22 — Scenario C requirement clarification (C1–C6) and clarified statement

**Intent:** Clarify Scenario C's single, intentionally under-specified product
statement ("Regulators need to be able to audit access to client account data")
before any code — different process from A/B, since there's no numbered spec to
extract ambiguities against; the clarification itself is the deliverable.

**AI produced (round 1):** Identified six ambiguities in the one-sentence statement:
who "regulators" are and how they interact with the system (C1 — flagged as the one
genuine fork worth confirming, given how much scope it determines: external
regulator-facing access vs. internal compliance staff producing reports); what
"access" means (C2 — leaned read/view specifically); what "client account data"
means given Scenario A's free-form `resourceType` (C3); what the actual deliverable
is (C4 — proposed framing this as an application of Scenario B's signed bulk export
rather than new infrastructure); regulatory-framework specificity (C5 — proposed not
targeting any named regulation); and auth/authz scope (C6 — proposed keeping it out
of scope, closing a question explicitly deferred back in Scenario A's 2a decision).
Proposed a draft clarified requirement statement building on all six.

**User feedback:** Confirmed the overall frame (internal staff, reuse of A/B
infrastructure) as right for the use case and scope. Added a genuine gap the AI's
draft hadn't surfaced: "client account data" could be embedded inside a record's
`payload` even when that record's `resourceType` isn't itself an account-type (e.g. a
support-ticket record referencing an account number) — meaning `resourceType`-based
filtering alone wouldn't find such records. Noted this doesn't change the design, but
should be documented as part of the process, and pointed out that Scenario B's
export already filtering by `resourceId` *or* `actorId` independently gives partial,
already-existing mitigation.

**AI response:** Incorporated the user's point into C3 as an explicit documented
limitation (not silently assumed away): full-text/payload-content search is out of
scope, partially but not fully mitigated by existing independent `actorId`/time-range
filtering. Finalized the clarified requirement statement with explicit scope
boundaries (auth/authz out of scope, read-path instrumentation out of scope,
`resourceType`-based account-data scoping, payload-content search limitation) and
wrote the full C1–C6 writeup plus the clarified statement into
`docs/REQUIREMENTS.md`.

**Decision:** **FINAL** for the clarification phase — all six ambiguities resolved,
clarified requirement statement locked in.

**Rationale:** See `docs/REQUIREMENTS.md`, Scenario C, "Ambiguities Identified" and
"Clarified Requirement Statement." Next: translate into a concrete technical design
(endpoint(s)/parameters) and an explicit implemented-vs-scoped-out boundary.

---

## 2026-08-22 — Scenario C technical design + docs/TASKS.md created for all three scenarios

**Intent:** User directed creating `docs/TASKS.md` — a companion document to
`docs/REQUIREMENTS.md`, holding the concrete technical design and actionable task
breakdown (Core Requirement #2: task decomposition with dependencies and sequencing)
derived from all decisions across Scenarios A, B, and C. Also the point where
Scenario C's remaining "translate into technical design" step needed to happen.

**AI produced:** Scenario C's technical design: identified that the clarified
requirement's three scoping dimensions (account/actor/time-range) map almost
entirely onto Scenario B's export, with one real gap — B's export only filtered by
`resourceId`/`actorId`, no `eventType` or time-range filter, which C's clarified
statement needs. Concluded the design is a small, targeted extension (add optional
`eventType`/`from`/`to` params to the existing export endpoint) rather than a new
`/audit/compliance-report` route, avoiding duplicated signing/bundling logic.

Then created `docs/TASKS.md` in full: a per-scenario task breakdown (mirroring
`docs/REQUIREMENTS.md`'s structure) covering data layer, write path, query path,
verify path, and tests for Scenario A; redaction, retention, and bulk export
(including the Scenario C filter extension folded directly into the export task
rather than listed as a separate later addition) for Scenario B; and
documentation/validation tasks for Scenario C. Each task cites the specific
`REQUIREMENTS.md` decision ID(s) it implements and its dependencies on other tasks.
Tasks were written against the *final*, amended state of each design (e.g. the
schema task includes Scenario B's per-field commitment and archival columns from the
start, not as a later migration) rather than replaying the chronological discovery
order. Closed with a suggested build order across all three scenarios.

**Decision:** Delivered as directed; no open questions raised back to the user for
this step, consistent with "proceed."

**Rationale:** See `docs/TASKS.md` in full. `docs/REQUIREMENTS.md`'s Scenario C
"Next steps" updated to point here instead of restating the now-completed design
step.

---

## 2026-08-22 — Implementation begins: A1 (data layer)

**Intent:** Start actual implementation, following `docs/TASKS.md`'s suggested build
order. First unit: A1 — the `audit_events` model, its migration, and the DB
roles/grants migration.

**AI produced:** `AuditEvent` SQLAlchemy model (`sequence_number` used directly as
primary key rather than a separate surrogate id, since it's already the app-computed
unique chain-order key from 7c — simplification over the original task wording's
implied separate `UNIQUE` constraint). Alembic migration with query-filter indexes.
A second migration provisioning `app_role`/`maintenance_role`.

**Refinement made during implementation, flagged rather than silently applied:**
while implementing the roles migration, found that `maintenance_role` needed
`INSERT` in addition to its column-scoped `UPDATE` — TASKS.md's B1.2/B2.2 call for
redaction/retention to append their own system event "in the same transaction" as
the column update, which isn't possible across two different roles/connections.
Granted `maintenance_role` both. Also found the least-privilege design as originally
decided had no enforcement mechanism — every connection, including the running
app's, used the Postgres superuser-equivalent account (`audit`), since
`Settings.database_url` was a single value. Split it into three:
`database_url` (`app_role`, what the app actually runs on),
`maintenance_database_url` (`maintenance_role`), and `admin_database_url`
(superuser, Alembic only, never imported by application runtime code).

**Verification, not just implementation:** ran a fresh `docker compose up` from an
empty volume to confirm migrations execute automatically before the app starts
(added a `command` override: `alembic upgrade head && uvicorn ...`); queried
`information_schema.column_privileges` to confirm the granted columns exactly match
the design (`maintenance_role` has `UPDATE` on the ten intended columns and *not* on
`sequence_number`/`content_hash`/`prev_hash`); ran a negative test connecting
directly as both `app_role` and `maintenance_role` via `psql` and confirmed both get
`permission denied` attempting to `UPDATE content_hash` — the least-privilege design
is actually enforced, not just documented. Full quality gates (ruff, mypy, pytest)
pass.

**Decision:** Proceeding task-by-task per `docs/TASKS.md`'s build order; checking in
at natural checkpoints (task groups) rather than per-file, consistent with the
pacing established during the design phase.

**Rationale:** See `docs/TASKS.md`, task entries A1.1–A1.3 (marked complete, each
with a pointer to the file(s) it produced and notes on refinements made during
implementation).

---

## 2026-08-22 — Implementation: A2 (write path)

**Intent:** Build the hashing module, append service, request/response schemas, and
`POST /audit/events` endpoint — the second unit in `docs/TASKS.md`'s build order.

**AI produced:** `core/hashing.py` (canonicalization, per-field salted commitments,
content_hash, record_hash), `services/append.py` (advisory-lock-scoped append),
`schemas/event.py` (Pydantic validation matching 1a–1d, camelCase API surface via
`alias_generator=to_camel` — an addition over the original task description, since
the source document's own examples use camelCase field names), and
`api/events.py` + wiring into `main.py`.

**Two implementation-level refinements, not deviations from `REQUIREMENTS.md`
decisions:** (1) `field_hash` hashes `[salt, key, value]` as one canonical JSON
array rather than concatenating three separately-canonicalized strings, avoiding
ambiguity from variable-length-string concatenation — functionally equivalent to
the documented `SHA256(salt || canonical(key) || canonical(value))` formula, just a
safer literal encoding of it. (2) Added `SessionDep`, a shared `Annotated` FastAPI
dependency alias in `core/db.py`, after `ruff` flagged the direct
`Depends(get_session)` pattern (B008) — avoids repeating the pattern (and
suppressing the lint warning) across what will be several more endpoints.

**Verification, not just implementation:** wrote `tests/test_hashing.py` early
(covers most of A5.1/A5.2 ahead of schedule — canonicalization determinism, salting
produces different hashes for identical low-entropy values, content_hash sensitivity
to every covered field including `sequence_number`, `record_hash`'s sensitivity to
`prev_hash`). Then live-tested against the actual running stack: wrote two events
through the real API, independently recomputed record 1's `recordHash` in a Python
shell and confirmed it matches record 2's stored `prev_hash` byte-for-byte — not just
"looks plausible" but arithmetically verified. Confirmed a malformed `eventType`
(lowercase) is rejected with 422 and consumes no `sequence_number` (checked via
direct DB query), validating 1d's "rejected writes never touch the chain" guarantee
end-to-end, not just in the abstract. Full quality gates (ruff, mypy, pytest) pass.

**Decision:** `docs/TASKS.md` updated: A2.1–A2.5 checked off with file pointers and
refinement notes; A5.1/A5.2 marked partially done (the parts coverable without A3/A4
existing yet), with what's still open stated explicitly rather than checked
prematurely.

**Rationale:** See `docs/TASKS.md`, task entries A2.1–A2.5, A5.1, A5.2.

---

## 2026-08-22 — Implementation: A3 (query path)

**Intent:** Build cursor pagination and `GET /audit/events` — the third unit in
`docs/TASKS.md`'s build order.

**AI produced:** `services/query.py` (`list_events`, filtering + keyset pagination)
and the `GET /audit/events` route in `api/events.py`. Pagination implemented as
"fetch `limit + 1` rows" to detect a next page without a separate `COUNT` query.

**Refinement, not a deviation:** ruff's B008 (no function calls in argument
defaults) flagged 2 of 9 identical `Query(...)` parameter defaults inconsistently
(same pattern, only `from_`/`to` flagged) — rather than debug the inconsistency,
switched all nine to FastAPI's `Annotated[type, Query(...)]` style, the same fix
already applied to the session dependency in A2.

**Noted, not fixed — a real but expected interaction:** while writing
`list_events`'s docstring, identified that `includeArchived=true` combined with any
other filter will never surface an archived record, since archival (Scenario B)
nulls every detail column and NULL never satisfies a SQL equality/range comparison.
Documented explicitly in the code rather than left for someone to discover and
mistake for a bug — it's the correct, if non-obvious, consequence of 1e's already-
accepted limitation.

**Verification:** live-tested against the real running stack rather than relying on
unit tests alone — seeded 7 records spanning multiple actors/resourceTypes/
eventTypes/timestamps (including 2 left over from A2's smoke test, confirming state
persists correctly across sessions), then confirmed: AND semantics between filters,
`resourceType`-alone filtering (4c), time-range filtering against the caller
timestamp specifically (4a) — including a case where two earlier-written records had
later timestamps than newly-seeded ones, correctly included by a `from` filter —
3-page cursor pagination across all 7 records with no gaps or duplicates, and
default-exclude/`includeArchived=true`-include behavior against a manually-archived
record. Automated pytest coverage needs a real Postgres fixture (per the
testing-approach decision, not SQLite) — deferred to A5 as originally sequenced, not
skipped.

**Decision:** Proceeding to A4 (verify path) next.

**Rationale:** See `docs/TASKS.md`, task entries A3.1, A3.2.

---

## 2026-08-22 — Implementation: A4 (verify path) — full req 9 acceptance test run live

**Intent:** Build the chain walk, violation taxonomy, and `GET /audit/verify` — the
fourth unit in `docs/TASKS.md`'s build order, and the point where req 9's core
acceptance criterion becomes testable end-to-end.

**AI produced:** `services/verify.py` (fail-fast, DB-streamed walk implementing the
genesis/content/link checks and the three-category taxonomy from 8a) and
`schemas/verify.py`. Required a small refactor to `hashing.py`, done first and
explained to the user before building verify on top of it: separated
`payload_commitment_from_hashes` (plain `{field: hash}` input) from the write-time
`payload_commitment` convenience wrapper, and changed `compute_content_hash` to take
an already-computed commitment value rather than the raw commitments dict — lets
verify's mixed fresh/retained-hash reconstruction and the append service's all-fresh
computation share one function. Also narrowed the model's
`payload_field_commitments` type from `dict[str, object]` to `dict[str, dict[str,
str]]`, since its shape is fixed and self-controlled.

**Verification — this is the most consequential testing done in the project so far,
since it's the literal req 9 acceptance test, not just component-level checks:** ran
the full write → verify → tamper → verify cycle for real against the running stack,
covering every case the design work anticipated: empty-chain intact; clean-write
intact; direct `payload` tampering → `CONTENT_MISMATCH`; direct `prev_hash`
tampering → `LINK_MISMATCH` (reverted using the hashing module itself to compute the
correct value, not guessed); outright deletion of an interior record →
`LINK_MISMATCH` naming exactly which `sequence_number` is missing; both
genesis-violation paths from 8a's design discussion (record 1's `prev_hash` tampered
vs. record 1 deleted entirely, producing different detail messages); a manually
simulated archived record producing no false positive (2b); a manually simulated
redacted field producing no false positive (3f); **and**, critically, confirmed that
tampering a *different, non-redacted* field in that same partially-redacted record
still produces `CONTENT_MISMATCH`. That last case is the literal property that
justified rejecting a whole-payload hash in favor of per-field commitments during
Scenario B's 3a discussion — reasoned about on paper then, empirically confirmed in
the running system now.

**Open item flagged for the user, not silently deferred:** automating this as a
`pytest` integration test needs a real-Postgres fixture (`testcontainers`, already a
dev dependency, not yet wired up). Manual live verification is thorough but isn't
part of the `pytest` suite reviewers would run. Framed as a decision point: build the
fixture now, or continue through Scenario B first and consolidate automated
test-writing into one later pass.

**Rationale:** See `docs/TASKS.md`, task entries A4.1, A4.2, A5.3, A5.4.

---

## 2026-08-22 — Implementation: B1 (redaction)

**Intent:** Build the redaction endpoint — the first Scenario B feature, per the
user's decision to continue there rather than pause for test infrastructure.

**AI produced:** `services/redact.py` (validates record exists/not archived/field
exists/not already redacted; appends a `FIELD_REDACTED` event and overwrites the
field's raw value with the marker, in one transaction), `api/redact.py`,
`schemas/redact.py`. Added `MaintenanceSessionDep` to `core/db.py`, mirroring
`SessionDep`. Extracted `is_redaction_marker` (previously duplicated inline in
`verify.py` during A4, now also needed by `redact.py`) into a new
`core/redaction.py` — one definition shared by the code that writes the marker and
the code that must recognize it, rather than two copies that could drift.

**Verification — the most important check here is a negative one:** wrote a record
with a sensitive field, redacted it via the real endpoint, and confirmed the
record's `content_hash` is byte-for-byte identical before and after — the direct,
empirical proof that redaction never touches the hash chain, not just an inference
from the design. Confirmed `/audit/verify` stays intact, confirmed the
`FIELD_REDACTED` event is discoverable via the query endpoint. Tested all four error
paths (404/404/409/409). Confirmed via direct `psql` as `app_role` that `app_role`
genuinely cannot perform the `UPDATE` — the least-privilege split from A1.3 actually
holds under the real feature that needs it, not just in isolation. Tampered the
*retained* hash for a redacted field directly and confirmed `CONTENT_MISMATCH` still
fires — empirically confirms the retained hash is transitively protected via
`content_hash`, not a trusted exemption from the chain's guarantee.

**Decision:** Proceeding to B2 (retention) next, per the build order's note that B1
and B2 can proceed independently.

**Rationale:** See `docs/TASKS.md`, task entries B1.1, B1.2.

---

## 2026-08-22 — Implementation: B2 (retention)

**Intent:** Build the retention sweep — the second Scenario B feature.

**AI produced:** `services/retention.py`, `api/retention.py`,
`schemas/retention.py`, `retention_window_days` setting in `core/config.py`.

**Design point resolved during implementation, not settled in `REQUIREMENTS.md`:**
which timestamp determines retention eligibility. Chose `recorded_at`
(server-assigned) over the caller-supplied `timestamp`, for the same trust-boundary
reason already established for chain order (7d) and the query time-filter split
(4a) — a caller shouldn't be able to influence their own record's retention
eligibility by misreporting when it happened.

**Correctness gap found and fixed, not in the original task description:** while
implementing, noticed that reading candidate records and then archiving them wasn't
itself serialized — two concurrent sweep calls could both see the same candidates
before either commits, producing duplicate `RECORD_ARCHIVED` events over the same
records. Fixed by acquiring the append service's advisory lock explicitly at the
start of the sweep, before the candidate query — reentrant-safe within a
transaction, so calling `append_event` later in the same function (which acquires
it again) is harmless.

**Verification:** live-tested against the real stack using a technique worth
noting — since `recorded_at` is server-assigned and can't be set through the write
API, backdated it via direct SQL to simulate an old record, then ran the sweep
against the actual default 365-day window (not a shortened test-only window).
Confirmed correct selective archival, `RECORD_ARCHIVED` event content, chain
integrity (`content_hash`/`prev_hash` unchanged, verify stays intact), default-query
exclusion, idempotency (second sweep call archives nothing), and — via direct
`psql` as `app_role` — that the role split holds for this third caller of
`maintenance_role` too, not just the two already tested (A1.3, B1).

**Decision:** Proceeding to B3 (bulk export) next.

**Rationale:** See `docs/TASKS.md`, task entries B2.1, B2.2.

---

## 2026-08-22 — Implementation: B3 (bulk export) — independent third-party signature verification, plus a real cross-feature gap found and resolved with the user

**Intent:** Build Ed25519 bundle signing and `GET /audit/export` — the last
Scenario A/B feature before a consolidated automated-testing pass.

**AI produced:** `core/signing.py` (Ed25519, dev-fixed seed, documented production
caveat — same pattern as DB passwords), `services/export.py`, `api/export.py`,
`schemas/export.py`. Added `cryptography` as a dependency. Extracted
`build_filtered_query` out of `services/query.py` so the paginated query endpoint
and export share one filter implementation with different defaults
(`include_archived` false vs. true) rather than two copies.

**Correctness requirement identified and solved, not in the original task
description:** the export bundle's signature covers a canonical serialization of
its content, so the JSON response actually served must be byte-identical to what
was signed — relying on Pydantic's default datetime formatting to coincidentally
match the project's own `canonical_timestamp` function would have been fragile.
Added `CanonicalDatetime`, a `PlainSerializer` type alias forcing every datetime
field in the relevant schemas through `canonical_timestamp` by construction, and
had the export service derive the signable bytes from the finished
`ExportBundle` model's own `model_dump()` (excluding only the signature field)
rather than hand-building a parallel dict that could drift from the real response.

**Verification — the most rigorous check in the project so far, since it
simulates a genuine external recipient rather than testing our own code path:**
exported a bundle, fetched the public key via the new endpoint, then in a
*separate* Python process — using only `cryptography`'s primitives directly, no
project code — reconstructed the canonical JSON from the bundle's own content and
verified the signature: valid. Tampered a record's payload *and* its own
`contentHash` together in the downloaded file (the exact attack self-consistency
checks alone can't catch, which was the whole reason 5a chose signing over
self-consistency) and re-verified: correctly invalid. Confirmed redacted fields
export and verify correctly, and the required-filter (400) validation.

**Real cross-feature gap found via this live testing, surfaced to the user rather
than silently patched:** archiving a record (Scenario B's retention, already built
and locked) nulls every column export can filter on, so an archived record becomes
practically unreachable through `actorId`/`resourceType`/`resourceId`/`eventType`/
time-range filters — 5e's "yes, archived records can be exported" holds in
principle but not in practice through this endpoint. This is more consequential for
Scenario C specifically: a regulator's natural ask ("complete history including
anything archived") can't be satisfied here. Presented three options — document as
a known limitation, add sequence-number-range export filters, or reopen what
archival nulls (Scenario B 1d/2a/2b) — rather than picking one unilaterally, since
two of the three touch already-locked design decisions. **User chose to document
it.** Written up in full in `docs/REQUIREMENTS.md` under Scenario B's 5e and cross-
referenced from Scenario C's clarified requirement, rather than left implicit.

**Decision:** B3 complete. Scenario A and B feature work is now done; per the
earlier user decision, next is building the deferred `testcontainers`-based
automated test suite (A5/B4) before Scenario C's remaining documentation/test tasks
(C1/C2).

**Rationale:** See `docs/TASKS.md`, task entries B3.1, B3.2; `docs/REQUIREMENTS.md`,
Scenario B item 5e and Scenario C's Clarified Requirement Statement.

---

## 2026-08-22 — Consolidated automated test suite (A5.3/A5.4, B4.1–B4.5) — a real crash bug found and fixed by testing against real Postgres

**Intent:** Build the deferred `testcontainers`-based Postgres fixture and codify
everything verified manually across A4–B3 into real `pytest` coverage, per the
user's earlier decision to defer this until Scenario A/B's features were all built.

**AI produced:** `tests/conftest.py` — session-scoped container (only starts for
test modules that actually request DB fixtures, so `test_hashing.py`/
`test_health.py` stay fast), migrations run via a subprocess with
`ADMIN_DATABASE_URL` overridden (avoids fighting the app's module-level
engine/settings singletons, which get bound to local-dev config the moment any
earlier test imports `audit_log_service.main`), function-scoped truncation between
tests, and three session fixtures (`app_session`/`maintenance_session`/
`admin_session`) matching the three DB roles. Five new test modules:
`test_acceptance.py` (req 9's full flow, every violation type), `test_concurrency.py`
(20 concurrent writers), `test_redaction.py`, `test_retention.py`, `test_export.py`
(independent signature verification via `cryptography` primitives, not the app's own
code), `test_privileges.py`.

**Environment note, not a design decision:** this environment's shell doesn't carry
`docker` group membership by default even though it was granted earlier in the
project (see the A1 log entries) — confirmed `testcontainers` needs direct daemon
access (unlike `docker compose`, which was being invoked through a wrapper). Running
pytest itself needs the same `sg docker -c "..."` wrapping used throughout for
`docker compose`.

**Infrastructure bug found and fixed, not a design gap:** the first full run failed
every DB-backed test with `"another operation is in progress"` — a session-scoped
async engine (`db_engines`) combined with pytest-asyncio's default per-test event
loop meant later tests tried to reuse asyncpg connections created under a different
loop. Fixed via `asyncio_default_fixture_loop_scope = "session"` /
`asyncio_default_test_loop_scope = "session"` in `pyproject.toml`.

**A real application bug found by testing against actual Postgres, not a test
artifact:** `test_direct_content_tampering_is_detected` (wholesale payload
replacement via direct SQL — a plausible, simple form of the exact tampering req 9
asks the system to catch) crashed `verify_chain` with an unhandled `KeyError`
instead of reporting `CONTENT_MISMATCH`. Root cause: `_recompute_content_hash`
assumed every key in a record's current `payload` has a matching entry in
`payload_field_commitments` — true for every legitimate write, but false the moment
an attacker adds or renames a payload key directly in the datastore, which is
exactly what req 9's validation scenario is testing for. Fixed in
`services/verify.py`: a payload key with no matching commitment falls back to an
empty salt, which can never coincidentally match the real 128-bit random salt, so
it reliably produces `CONTENT_MISMATCH` through the existing comparison logic
instead of raising. Locked in as a named regression test rather than just a passing
assertion, so the reason it exists survives future refactors. This is the clearest
example in the project of automated testing against a real database finding
something that manual `curl`-based verification (which only exercised value edits,
never key addition) had not.

**A test bug found and fixed, distinct from the app bug above:**
`test_field_redacted_event_is_discoverable` asserted `redact_field`'s return value
*was* the `FIELD_REDACTED` event; it actually returns the redacted target record
(matching `api/redact.py`'s actual contract). Split into two correct tests rather
than just patching the assertion, since both are genuinely worth covering
separately.

**Quality-gate follow-through, not scope creep:** `bandit` (already a configured
gate) flagged nine `assert`-based type-narrowing statements added during A4/B1
(B101 — asserts are stripped under Python's `-O`, so logic a caller depends on
shouldn't rely on one holding). Added `core/invariants.py::require_not_none` and
replaced all nine, since bandit was already part of this project's stated quality
gates and silently ignoring its findings would contradict that.

**Verification:** full suite (41 tests) passes against real Postgres in ~12 seconds;
`ruff`, `mypy`, `bandit`, and `pip-audit` all clean.

**Decision:** This closes out all previously-deferred A5/B4 test tasks. Remaining
work per `docs/TASKS.md`'s build order: C1/C2 (Scenario C's documentation and
validation test — no new implementation, since C's technical design was already
folded into B3.2).

**Rationale:** See `docs/TASKS.md`, task entries A5.3, A5.4, B4.1–B4.5.

---

## 2026-08-22 — Scenario C completion: C1 (documentation), C2 (validation test)

**Intent:** Close out the last remaining tasks in `docs/TASKS.md` — Scenario C's
usage documentation and a validation test proving the compliance scenario actually
works end-to-end, not just that its constituent pieces (export, filters, signing)
each work in isolation.

**AI produced:** A new "Compliance reporting (Scenario C)" section in `README.md` —
a concrete example request, an explanation of why `eventType` is caller-chosen
rather than a fixed enum (ties back to 1a), and the explicit scope boundaries from
the Clarified Requirement Statement, including the archived-record export
limitation found during B3. Added a note to the "Tests" section about `sg docker`
being required for the Postgres-backed integration tests in this environment.

`tests/test_compliance.py`: three events for the same account — one access event
inside the requested window (should appear), one non-access event inside the window
(should be excluded by the `eventType` filter), one access event outside the window
(should be excluded by `from`/`to`) — deliberately constructed so the test proves
the *combination* of filters correctly isolates the intended subset, not merely
that each filter works when applied alone. Independently verifies the resulting
bundle's signature via `cryptography` directly, same pattern as `test_export.py`.

**Verification:** full suite (42 tests) passes; `ruff`/`mypy` clean.

**Decision:** This completes every task in `docs/TASKS.md` across all three
scenarios — implementation, documentation, and automated validation.

**Rationale:** See `docs/TASKS.md`, task entries C1, C2.

---

## 2026-08-23 — Remaining assignment deliverables: ATTESTATION.md, ARCHITECTURE.md, TESTING.md, ENGINEERING_SUMMARY.md

**Intent:** Close the gap between what's built and the assignment's full §7
deliverable list. Took stock in the previous turn: prototype, three scenarios, and
AI usage log were done; attestation, architecture overview, testing/limitations
write-up, and final engineering summary were not.

**AI produced:** `ATTESTATION.md` at the repo root, deliberately left as a template
(name and dates as placeholders) rather than filled in — per the user's explicit
instruction to fill it out themselves; email pre-filled since it's already known
and unambiguous (attribution use, not a judgment call). `docs/ARCHITECTURE.md`
(components with a Mermaid diagram, data model table, API surface table, hash chain
design explained as three layers with why each is necessary, concurrency model,
security model, known architectural limitations) — synthesized from and linking
into `REQUIREMENTS.md` rather than duplicating its rationale. `docs/TESTING.md`
(approach, the two bugs testing itself found, coverage table by area, explicit
"what isn't automated and why" section). `docs/ENGINEERING_SUMMARY.md` (process
narrative, artifact inventory, three concrete risks-found-and-fixed examples,
consolidated trade-offs/assumptions/limitations, each cross-referenced rather than
restated in full). Updated `README.md` with a documentation index table.

**Verification, not just writing:** double-checked the API surface table and DB
role table in `ARCHITECTURE.md` against the actual router source
(`grep` for `@router.get/post` and `SessionDep`/`MaintenanceSessionDep` usage)
rather than writing from memory of what was built. Caught and fixed a real Mermaid
syntax error in the architecture diagram — dotted-edge labels need spaces
(`-. text .->`, not `-.text.->`) — before it could render broken in an actual
review. Corrected a vague "12+ commits" figure to the precise count from `git log`.

**Decision:** Ready for the user's stated next step — a thorough review of the
documents and test cases, after which further tests or updates may follow.

**Rationale:** See `ATTESTATION.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md`,
`docs/ENGINEERING_SUMMARY.md`, and the updated `README.md` documentation index.

---

## 2026-08-23 — User review: apparent contradiction in ARCHITECTURE.md's concurrency section

**Intent:** User, reviewing the newly-committed docs, questioned a sentence in
`ARCHITECTURE.md`'s Concurrency section — "a gap in the sequence can only mean
tampering, never a benign retry" — as seemingly outdated, in apparent tension with
the earlier decision (`REQUIREMENTS.md` 8a) that a dedicated `SEQUENCE_GAP` check
was considered and rejected as adding no detection coverage beyond `LINK_MISMATCH`.

**AI analysis:** Distinguished two different claims that had been conflated: 8a is
about **check design** (verify doesn't need a *separate* gap-scanning mechanism,
since interior gaps already surface via `LINK_MISMATCH`); the concurrency sentence
is about **causality** (given 7c's design — app-computed `sequence_number`, no DB
`SERIAL`, advisory-lock serialization instead of retry-on-conflict — a gap can only
be caused by deletion, never a benign retry artifact). Argued these aren't
contradictory but complementary: the causal guarantee is what *justifies* 8a's
simplification — `LINK_MISMATCH`'s gap-naming message is only safely reportable
without a hedge ("...or possibly a retry") because 7c already rules that
alternative out. Concluded the sentence wasn't substantively wrong, but was a real
documentation problem: sitting alone with no cross-reference to 8a, it reads as if
it might be asserting gap-checking as a mechanism, which is exactly what would
contradict 8a.

**Decision:** **FINAL.** Revised the sentence to state the guarantee explicitly as
a prerequisite for 8a's design rather than a freestanding claim, with an explicit
cross-reference. User confirmed this resolved the concern.

**Rationale:** See `docs/ARCHITECTURE.md`, "Concurrency" section. No change to
`REQUIREMENTS.md` 7c or 8a themselves — both already correctly reflected this
relationship; only `ARCHITECTURE.md`'s synthesis needed the connection made
explicit.

---

## 2026-08-23 — Production-readiness extensions: HTTP-layer tests, write-throughput load test

**Intent:** User, after reviewing the docs, asked for production-readiness
improvements beyond the three-scenario scope. Proposed a menu (performance/load
testing, CI pipeline, structured logging, secrets externalization, HTTP-layer
tests, consistent error handling); user chose HTTP-layer tests followed by a
write-throughput load test, explicitly sidelining the rest for now.

**AI produced:** A `client` fixture in `tests/conftest.py` using FastAPI's
`app.dependency_overrides` to redirect `get_session`/`get_maintenance_session` at
the test container, rather than environment-variable manipulation of the app's
module-level engine singletons (same reasoning already applied to the migration
subprocess). `tests/test_http.py` — 11 tests covering each endpoint's HTTP-specific
behavior (status codes, response shape) plus one full req 9 acceptance flow through
the real HTTP surface end to end, proving the routing/validation/DI/serialization
wiring is correct as a whole. `tests/test_load.py` — 100 concurrent writes through
the real HTTP layer, asserting correctness (gapless chain, all succeed) rather than
a hard throughput threshold (hardware varies too much across machines/CI for a
fixed number to be a meaningful, non-flaky gate), reporting throughput as
informational output instead.

**Result, not simulated:** measured ~55 writes/sec sustained under 100 concurrent
requests in this environment — turns 7c's "fully serialized appends" from a
qualitative, accepted trade-off into an actual number. All 11 HTTP tests passed on
the first run against real Postgres, unlike the earlier service-layer suite (no new
bugs found this time) — consistent with the HTTP wiring having already been
extensively live-verified via `curl` during each feature's original development;
this pass captured that verification as repeatable automated coverage rather than
finding new defects.

**Documentation kept in sync, not left stale:** added a new "Production-readiness
extensions" section to `TASKS.md` (explicitly distinguished from the original
three-scenario task list, since this work wasn't driven by a numbered requirement),
updated `TESTING.md`'s coverage table and "what isn't automated" section (HTTP
layer and write throughput moved from gaps to covered; verify-walk-at-scale named
as the next specific load-testing target), and updated `ARCHITECTURE.md`'s
"fully serialized appends" limitation with the measured figure.

**Decision:** Per the user's prior instruction, no commit made — user is committing
their own review changes from this point forward.

**Rationale:** See `docs/TASKS.md`, "Production-readiness extensions" (P1, P2);
`docs/TESTING.md` coverage table and gaps section; `docs/ARCHITECTURE.md`,
"Known architectural limitations."

---

## 2026-08-25 — README: "Using the API" section

**Intent:** User asked for instructions on querying the API endpoints in
`README.md` — a general usage walkthrough had never been written; only the
Scenario C compliance-reporting section had a worked example (for `/audit/export`
specifically).

**AI produced:** A new "Using the API" section (placed after "DB roles," before
"Tests") covering every endpoint not already documented: write, query, verify,
redact, retention sweep, with a one-line pointer to the existing Compliance
Reporting section for export/public-key rather than duplicating that walkthrough.
Each entry pairs a `curl` example with the key behavioral notes a caller would need
(filter semantics, pagination, error codes, idempotency) rather than just the
request shape.

**Verification, not assumed accuracy:** brought the real stack up and ran every
example in the new section exactly as written — write, query, verify, redact,
sweep, and (from the existing Compliance section) export and public-key — against
a clean database, confirming actual responses match what's documented before
treating the section as done. Tore the stack down afterward.

**Decision:** No commit made, per the user's standing instruction to commit their
own review changes from this point forward.

**Rationale:** See `README.md`, "Using the API" section.

---

## 2026-08-26 — Requirements change: Authentication & Authorization (Scenario C)

**Status:** PROVISIONAL — requirements only; implementation, tests, and other docs
not yet updated.

**Intent:** User reported that external review of the initial submission flagged
authentication and authorization as necessary for the prototype, and directed that
it be filed under Scenario C, starting with a requirements-level discussion before
any code changes.

**Context surfaced by AI, confirmed by user:** the assignment brief names no
explicit auth requirement — this falls under the evaluation criteria's "security
and production readiness," and the repository already had a directly relevant,
previously-**Decided** item: Scenario C's C6, which scoped auth/authz out on the
reasoning that regulators never authenticate to the system directly (C1). AI
proposed that this was the same question asked too narrowly the first time — C1
answered "who consumes a report," never "who may call the API at all" — and
recommended reopening C6 with a `⚠️ REVISED` note (matching the precedent already
set for item 6c) rather than silently rewriting it.

**AI produced, iterated with user:** a five-part proposal (C7 mechanism, C8
coverage, C9 role model, C10 actorId-trust change, C11 secrets handling) presented
in chat before any file was touched. Asked the user directly on C7 (the one
foundational fork: static API keys vs. JWT bearer tokens vs. mTLS client certs,
each with stated trade-offs) via a structured question; presented C8–C11 as
recommendations for the user to react to rather than open questions, since each
follows fairly directly from decisions (C1, 2a) already locked in.

**Accepted:** User selected static API keys (C7) as presented. C8–C11 were not
challenged and were written up as proposed:
- **C8:** auth coverage is system-wide (every endpoint except `GET /health` and
  `GET /audit/export/public-key`), not scoped to just Scenario C's endpoints —
  reasoning that partial auth wouldn't cohere as "this service has auth."
- **C9:** four flat roles (`writer`, `reader`, `compliance`, `scheduler`) mapped
  onto the actor conventions already used informally in the docs, with `export`
  placed under `compliance` (not `reader`) per C1's own framing of who export is
  for. Documented as additive to, not a replacement for, the existing DB-level
  `app_role`/`maintenance_role` split (2a).
- **C10:** identified as the one substantive new engineering decision this change
  produces, not just plumbing — redact/retention's `actorId` was a
  caller-asserted, previously-unauthenticatable claim about who performed a
  self-auditing administrative action; once callers are authenticated, continuing
  to trust that claim as free-form input would defeat the purpose of adding auth.
  Decision: derive `actorId` server-side from the authenticated principal for
  those two endpoints only, removing it from their request schemas. Write's
  `actorId` is unaffected — it identifies the subject of the recorded domain
  event, not the calling principal, so caller-supplied input remains correct
  there.
- **C11:** dev-fixed API keys via config, filed under the same
  already-documented secrets-externalization limitation as the export signing key
  and DB passwords, not solved now.

**Where this lives:** `docs/REQUIREMENTS.md`, Scenario C — C6 revised in place;
new subsection "Requirement Change — Authentication & Authorization (post-review)"
holds C7–C11; the Clarified Requirement Statement's auth scope-boundary bullet
updated to point at the new decision instead of asserting auth is out of scope;
"Next steps" appended with the concrete follow-on work (task breakdown, router/
service wiring, schema changes, test fixture changes, doc updates) so it isn't
lost.

**Not yet done:** `TASKS.md` task breakdown, actual implementation, test coverage
(including negative cases — missing/invalid key, wrong role per endpoint),
`ARCHITECTURE.md`/`README.md` updates. Tracked as this entry's explicit next step,
not silently deferred.

---

## 2026-08-26 — Requirements addition: cross-tenant/cross-account denial (C12)

**Status:** PROVISIONAL — requirements only.

**Intent:** User named a specific reviewer-requested security test —
"cross-tenant denial tests" — and asked what such a test would cover, before
deciding whether/how to add it.

**AI explained, then surfaced a gap:** cross-tenant denial tests generally verify
that an authenticated, otherwise-valid principal is still denied when acting on
another tenant's data specifically (catching IDOR-style bugs where a role check
passes but the underlying query isn't filtered to the caller's own data).
Mapping that onto this system exposed that C9's role model (`writer`/`reader`/
`compliance`/`scheduler`) is purely functional, with no per-account scoping at
all — as designed, any `reader`/`compliance` key can already see every account in
the log, so a cross-tenant denial test would currently have nothing to deny. This
wasn't a pre-existing decision being revisited; it was a new gap the question
itself surfaced.

**AI produced, user accepted:** C12 — API key principals may carry an optional
`resourceScope` allow-list of `resourceId`s, enforced server-side (intersected
into the query, not just checked against caller-supplied filters) for `reader`'s
query endpoint and `compliance`'s export endpoint specifically. Out-of-scope
requests denied with `404` (not `403`) to avoid confirming another account's
existence. Scoped to `resourceId` (the "account" analog per C3) rather than
`actorId`, which identifies *who acted*, not a unit of data ownership. `writer`
and `scheduler` deliberately left unscoped (write's `resourceId` describes the
event's subject, not the caller, per the same reasoning as C10; retention sweep
is age-based, not account-based) and `GET /audit/verify` deliberately left
unscoped (discloses no account-specific content to begin with — scoping it would
fight the single global chain design, 7a, for no confidentiality benefit).
Postgres row-level security was considered and rejected as the enforcement
mechanism — more production-grade, but requires schema/session-wiring changes
disproportionate to this exercise, and doesn't cleanly fit this schema's
`resourceId` granularity (RLS is typically keyed on a coarser `tenant_id` this
table doesn't have) — named as a documented production trade-off instead.

**Where this lives:** `docs/REQUIREMENTS.md`, Scenario C, new item C12 (after
C11); Clarified Requirement Statement's auth scope-boundary bullet updated to
reference it; "Next steps" appended with the concrete follow-on work, including
an explicit note that the test suite needs a dedicated cross-account denial test
(two scoped keys, each confirmed to reach only their own account and denied on
the other's).

**Not yet done:** everything — this is a requirements decision only.
`TASKS.md`/implementation/tests still pending, same as C7–C11.

---

## 2026-08-26 — Task breakdown: Authentication & Authorization (C3.1–C3.7)

**Status:** PROVISIONAL — task breakdown only, no implementation yet.

**Intent:** Translate the requirements decided in C7–C12 into an actionable,
dependency-ordered task list in `docs/TASKS.md`, matching the structure and
per-task traceability convention already used for Scenarios A/B/C (checkbox,
one-line description, `Implements:`/`Depends on:` back-references, target file
paths).

**AI produced:** a new "C3. Authentication & Authorization" section under
Scenario C — seven tasks (C3.1 principal/API-key config, C3.2 the auth+role
FastAPI dependency, C3.3 actorId-from-principal for redact/retention, C3.4
resource-scope enforcement for query/export, C3.5 auth/authz test coverage
including the breaking-change updates it forces on existing tests, C3.6 the
dedicated cross-account denial test, C3.7 documentation) — plus updated the
"Suggested build order" section with C3's internal dependency ordering (C3.3 and
C3.4 identified as independent of each other, both gated on C3.2; C3.6 gated on
C3.4; C3.7 last).

**Also updated:** the existing Scenario C task section's closing text, which
previously read "fully complete" and listed "authentication/authorization... is
out of scope (C6)" as an explicit non-omission — both now stale given C6's
reversal. Replaced with a pointer to the requirement change and to C3.

**Accepted as proposed** — no user changes to the breakdown itself; this is the
task-level decomposition of decisions already confirmed in the C7–C12
requirements pass, not a new design fork.

**Where this lives:** `docs/TASKS.md`, new "C3. Authentication & Authorization"
section and "Suggested build order" update.

**Not yet done:** all C3.1–C3.7 implementation and tests. Per the user's
instruction, a commit will follow once this task breakdown is reviewed, before
implementation begins.

---

## 2026-08-26 — Task addition: fail-secure guard on hardcoded dev secrets (P3)

**Status:** PROVISIONAL — task added, no implementation yet.

**Intent:** User relayed another code-review finding — "hardcoded dev secrets
with no fail-secure guard" — and asked what it referred to and how to fix it,
unrelated to the auth/authz work in progress.

**AI explained:** the finding maps to `core/config.py`'s `Settings` — real
secret material (`app_role_password`, `maintenance_role_password`,
`export_signing_key_seed_hex`, and the same passwords again embedded in the
three `*_database_url` fields) hardcoded as Python defaults, already named as a
sidelined limitation in `TASKS.md` ("no 'production mode' guard preventing
accidental reuse"). Clarified the specific gap the review comment is pointing
at: not that dev defaults exist (normal, fine for local/test use), but that
nothing checks whether a real deployment actually overrode them — the app fails
*open*, booting silently on dev secrets in any environment including
production.

**AI proposed, user accepted, scoped deliberately narrower than full secrets
externalization:** an `environment` setting (`development`/`test`/`production`,
default `development`) plus a `model_validator` on `Settings` that raises at
construction time if `environment == "production"` and any of the three secret
fields still match their hardcoded literal. No vault/KMS integration — that
stays out of scope, named explicitly as still-sidelined. Chosen because it's a
small, testable, fail-closed guard that directly answers the review finding,
distinct from (and much smaller than) actually sourcing secrets from a manager.

**Where this lives:** `docs/TASKS.md`, "Production-readiness extensions" — new
`P3` task (following the P1/P2 precedent: filed directly as a task with inline
rationale, no separate `REQUIREMENTS.md` ambiguity-resolution entry, consistent
with how P1/P2 were handled since these aren't part of the three-scenario
process). "Explicitly sidelined" bullet updated to distinguish "fail-secure
guard" (P3, now in progress) from "full secrets externalization" (still out of
scope).

**Not yet done:** implementation and the two tests (raises under
`environment="production"` with defaults; doesn't raise in `"development"` or
with real overrides).

---

## 2026-08-26 — Implementation: Authentication & Authorization (C3.1–C3.7)

**Status:** FINAL for this task set — implemented, tested, live-verified.

**Intent:** Build out the C7–C12 requirements / C3.1–C3.7 task breakdown decided
in the two prior entries: static API-key auth, role-based authorization,
`actorId` derivation from the authenticated principal for redact/retention, and
resource-scoped cross-account denial.

**AI produced, task by task:**
- **C3.1/C3.2:** `Principal` (BaseModel: `principal_id`, `roles: frozenset[str]`,
  `resource_scope: frozenset[str] | None`) and a dev-fixed
  `Settings.api_keys: dict[str, Principal]` in `core/config.py` — four keys, one
  per role, unscoped by default. New `core/auth.py`: `_authenticate` (reads
  `X-API-Key`, 401 on missing/unrecognized — deliberately not distinguished, so
  key-probing can't tell the two apart) and `require_roles(*roles)`, a dependency
  factory nesting `_authenticate` so 401 always precedes 403. Wired into every
  router (`api/events.py`, `api/verify.py`, `api/redact.py`, `api/retention.py`,
  `api/export.py`) per C9's table; `GET /health` and `GET /audit/export/public-key`
  deliberately left unguarded (C8).
- **C3.3:** Removed `actorId` from `RedactRequest`; removed
  `RetentionSweepRequest` entirely (it had no fields left once `actorId` was
  gone, so the endpoint takes no body now rather than keeping an empty schema).
  Both routers now pass `principal.principal_id` as `actor_id` to the service
  layer instead.
- **C3.4:** `resource_scope: frozenset[str] | None` parameter added to
  `build_filtered_query`/`list_events` (`services/query.py`) and `export_bundle`
  (`services/export.py`), intersected via `resource_id.in_(...)`. New
  `core/auth.py:check_resource_access` denies an explicitly-named out-of-scope
  `resourceId` with 404 before the query even runs; wired into
  `api/events.py`'s query route and `api/export.py`'s export route.
- **C3.5:** New `tests/test_auth.py` — parametrized 401/403/"correct role passes"
  checks across all six protected (method, path, role) combinations, plus a
  dedicated redact happy-path test confirming the `FIELD_REDACTED` event's
  `actorId` is the authenticated principal. Updated `tests/conftest.py` with an
  `auth_headers(role)` helper that looks up the configured dev key from
  `settings.api_keys` itself (not a duplicated hardcoded string), so a key
  rename in `config.py` can't silently desync from what tests send. Rewrote
  `tests/test_http.py` and `tests/test_load.py` to pass the correct role's
  headers on every call, and to drop `actorId` from redact/retention request
  bodies (the breaking changes flagged in the task breakdown).
- **C3.6:** New `tests/test_cross_tenant.py` — four tests using `monkeypatch` to
  inject two scoped test-only keys (kept out of the production `api_keys`
  defaults, which stay unscoped) rather than adding test fixtures to prod
  config: own-account access succeeds; the other account is denied by explicit
  `resourceId`; denied even when the filter is omitted entirely (the case an
  explicit-id check alone wouldn't catch); and denial holds symmetrically from
  the other scoped principal's side, guarding against a one-directional bug.
- **C3.7:** `ARCHITECTURE.md` — auth gate added to the components diagram, API
  surface table gained an "API role" column, new "Authentication &
  authorization" section, new known-limitation bullet (static keys, no real
  IdP). `README.md` — new "Authentication" section (dev key table), every
  "Using the API" and Compliance-reporting `curl` example updated with
  `X-API-Key`, `actorId` dropped from the redact/retention examples, the stale
  "no auth" scope-boundary bullet corrected. `TESTING.md` — test count
  (42 → 85), two new coverage-table rows.

**Verification, not assumed correctness:** `ruff`/`mypy`/`bandit` all clean.
Full suite: 85 passed (`sg docker -c "uv run pytest -q"`). Live-verified against
the real Docker Compose stack via `curl`: missing key → 401, invalid key → 401,
wrong role → 403, correct role → success for write/redact/retention/export/
verify; confirmed the `FIELD_REDACTED` system event's `actorId` is
`compliance-officer-1` (the authenticated principal) rather than caller input;
confirmed `GET /health` and `GET /audit/export/public-key` remain reachable with
no key at all. Stack torn down afterward.

**Rejected/deferred, not silently dropped:** a role-hierarchy (`compliance`
implying `reader`) — flat roles only, per C9; a `resourceScope` grant on
`writer`/`scheduler` — explicitly out of scope per C12's rationale, unchanged
from the requirements pass.

**Where this lives:** see file list above; no docs changes beyond what's listed.

---

## 2026-08-26 — Implementation: fail-secure secrets guard (P3)

**Status:** FINAL — implemented, tested, live-verified.

**Intent:** Implement the P3 task decided earlier: `Settings` should refuse to
construct under `ENVIRONMENT=production` while `app_role_password`,
`maintenance_role_password`, or `export_signing_key_seed_hex` still hold their
hardcoded dev-default values.

**AI produced:** `core/config.py` — the three dev-default literals extracted to
named module-level constants (`_DEV_APP_ROLE_PASSWORD`, etc.), reused both as
the field defaults and as the guard's comparison values, so the guard can't
silently drift from what the actual defaults are. New
`Settings.environment: Literal["development", "test", "production"] =
"development"` field, and a `@model_validator(mode="after")` that raises
`ValueError` (surfaces as `pydantic.ValidationError`) listing which specific
fields are still at their dev default, only when `environment == "production"`.
Bandit flagged B105 ("possible hardcoded password") on the two newly-named
password constants — a true false positive here (they're intentionally
dev-only, and now guarded by the validator that reads them), suppressed with
`# nosec B105` rather than restructuring the code to dodge the heuristic.

**Tests:** new `tests/test_config.py`, four tests, no DB fixture (pure
`Settings` construction, stays fast): raises under `production` with defaults;
doesn't raise under `production` with all three overridden; doesn't raise under
the default `development` environment even with defaults present; default
environment is `development`.

**Verification, not assumed correctness:** `ruff`/`mypy`/`bandit`/`pip-audit`
all clean. Full suite: 89 passed (up from 85). Live-verified directly against
`Settings`: `environment="production"` with defaults raises with the expected
message; with all three secrets overridden it succeeds; unset (`development`)
succeeds. Also rebuilt and brought up the real Docker Compose stack (default,
unset `ENVIRONMENT`) to confirm no regression — `/health` and a real write both
still work exactly as before this change. Stack torn down afterward.

**Where this lives:** `src/audit_log_service/core/config.py`,
`tests/test_config.py`; `docs/TASKS.md` (P3 marked done), `docs/TESTING.md`
(new coverage row, "sidelined" list clarified to distinguish this guard from
still-out-of-scope full secrets externalization), `docs/ARCHITECTURE.md` (P3
cross-referenced from the existing dev-fixed-API-keys limitation bullet).

---

## 2026-08-26 — Additional test coverage for auth/authz and P3

**Status:** FINAL — implemented, tested, live-verified.

**Intent:** User asked for tests covering "all the new functionality" from the
auth/authz and fail-secure-guard work, after committing it. Reviewed
`core/auth.py`'s branches against what `test_auth.py`/`test_cross_tenant.py`
already exercised to find genuine gaps rather than padding with redundant
tests — `check_resource_access`'s branches turned out to already be fully
covered; four real gaps were found.

**AI produced:**
- **Retention's `actorId` derivation was untested** — `test_auth.py` had a
  redact happy-path test proving `FIELD_REDACTED`'s `actorId` comes from the
  authenticated principal, but no equivalent for retention's
  `RECORD_ARCHIVED` event. Added the parallel test.
- **A caller sending a spoofed `actorId` to redact was silently ignored, not
  rejected** — found while writing the above: `RedactRequest` had no
  `extra="forbid"`, so a stray/attempted-spoof `actorId` in the body would
  just vanish rather than surface as a client error, which could mask a
  caller's misunderstanding of who's now on record as having performed the
  redaction. This is a small correctness fix flowing directly from C10's own
  intent, not a new design decision — added `extra="forbid"` to
  `RedactRequest`'s `model_config` and a test confirming `422`.
  (Retention's endpoint has no request body at all now, so there's no
  equivalent schema to harden — reintroducing an empty body schema there
  would force callers to always send `{}`, contradicting the "no request
  body" design already committed and documented; left as-is.)
- **`GET /audit/verify` staying unscoped for a scoped principal was only a
  documentation claim** (C12) — added a test proving a `reader` key with a
  `resourceScope` still gets the full-chain result, not a restricted one.
- **Scope intersection was only tested via `resourceId`/omitted-filter
  combinations** — added a test using `resourceType` (shared by both seeded
  accounts) without `resourceId`, confirming the intersection is genuinely
  keyed on `resourceId`, not incidentally satisfied by some other filter.

**Verification, not assumed correctness:** `ruff`/`mypy`/`bandit` all clean.
Full suite: 93 passed (up from 89). Live-verified against the real Docker
Compose stack: a spoofed `actorId` on redact returns
`{"detail":[{"type":"extra_forbidden",...}]}` / `422`; the same request
without `actorId` still redacts correctly. Retention's `actorId` derivation
relies on backdating `recorded_at` via direct DB access, which the automated
test does and the full suite confirmed passing — not re-demonstrated via curl,
since that would need the same DB-level manipulation the test already
performs. Stack torn down afterward.

**Where this lives:** `tests/test_auth.py` (3 new tests),
`tests/test_cross_tenant.py` (1 new test),
`src/audit_log_service/schemas/redact.py` (`extra="forbid"`).

---

## 2026-08-26 — Doc sync: ARCHITECTURE.md and ENGINEERING_SUMMARY.md

**Status:** FINAL.

**Intent:** User pointed out `ARCHITECTURE.md` and `ENGINEERING_SUMMARY.md`
needed updating for the auth/authz + fail-secure-guard work.

**Checked first, not assumed:** `ARCHITECTURE.md` turned out to already be
current — the components diagram, API surface table, and a full "Authentication
& authorization" section were added as part of task C3.7 earlier in this same
work stream. Confirmed via grep rather than re-doing it blind.
`ENGINEERING_SUMMARY.md` had genuinely not been touched since before this work
began, and contained a now-false claim.

**AI produced (`ENGINEERING_SUMMARY.md` only):**
- **Objective** — added a paragraph noting the post-submission extension and
  pointing at where it's explained.
- **Plan and process** — added a paragraph documenting that the auth/secrets-guard
  work ran through the *same* four-phase discipline (requirements → tasks →
  implementation → tests) as the original three scenarios, not an ad hoc patch.
- **Artifacts** — commit count corrected (14+ → 20+, actual `git log` count).
- **Risks found and mitigated** — added the redact `actorId`-spoofing gap found
  while writing auth test coverage (`extra="forbid"` fix) as a third example,
  which also fixed a pre-existing inconsistency: the section header said "Three
  concrete examples" but only two bullets existed — likely from the user's
  earlier direct edit removing a stale risk bullet without updating the count.
  Not called out further since it's a minor pre-existing doc drift, not new work.
- **Trade-offs** — added the C7 mechanism choice (static API keys vs. JWT vs.
  mTLS, with the rejected alternatives' reasoning).
- **Assumptions — the substantive fix.** The existing "No authentication/
  authorization layer" bullet was now false. Rewrote it to explain the reversal
  rather than deleting it outright: what was originally assumed, why (C6/C1),
  what external review actually challenged (C1 answered "who *consumes* a
  report," never "who may call the API"), and where the resulting decision
  lives (C7–C12). Kept deliberately visible rather than scrubbed, since a wrong
  assumption later corrected on challenge is itself part of the traceability
  record this document exists to provide. Also updated the dev-secrets bullet to
  mention P3's guard and its explicit boundary (guards the worst failure mode,
  doesn't solve secrets management).
- **Known limitations** — added static API keys / no real IdP as a third
  "most consequential" item, cross-referenced to `ARCHITECTURE.md` rather than
  duplicating its fuller treatment.
- **What's next** — rewritten from a forward-looking placeholder ("a review may
  follow") to reflect what the review actually produced so far (P1/P2, then
  C7–C12/P3), with remaining sidelined items still named explicitly.

**Where this lives:** `docs/ENGINEERING_SUMMARY.md` only — `docs/ARCHITECTURE.md`
needed no changes.
