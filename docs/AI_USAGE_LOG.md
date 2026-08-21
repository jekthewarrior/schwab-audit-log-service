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
