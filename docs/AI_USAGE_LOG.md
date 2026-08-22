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
