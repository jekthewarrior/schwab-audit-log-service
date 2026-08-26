# Final Engineering Summary

## Objective

A tamper-evident audit log service built across three scenarios: a core append-only,
hash-chained log (Scenario A); retention and field-level redaction extensions
(Scenario B); and a compliance-reporting capability for an intentionally
under-specified requirement (Scenario C). Built AI-assisted, engineer-led — every
design decision in this repository was proposed, reasoned about, and explicitly
confirmed before implementation, not generated and accepted wholesale.

Extended post-submission with API authentication/authorization and a fail-secure
secrets guard, driven by external code review rather than the original brief — see
[Plan and process](#plan-and-process) below for how that change ran through the
same requirement-clarification → task-decomposition → implementation → testing
discipline as the original three scenarios, and
[Assumptions](#assumptions) for the original no-auth assumption it reversed.

## Plan and process

Requirement analysis, design, and implementation ran as three distinct, sequential
phases rather than being interleaved per-scenario:

1. **Requirement clarification** (`REQUIREMENTS.md`) — for Scenarios A and B, raw
   requirements were extracted from the source document, ambiguities identified
   against each, grouped by dependency (foundational decisions before the ones that
   build on them), and resolved one at a time with rationale and rejected
   alternatives recorded. Scenario C, being intentionally under-specified, followed
   a different process: clarifying ambiguities in a single product sentence into an
   explicit, testable requirement statement with documented scope boundaries.
2. **Task decomposition** (`TASKS.md`) — every `REQUIREMENTS.md` decision converted
   into a concrete, dependency-ordered implementation task, each citing which
   decision(s) it implements.
3. **Implementation**, task group by task group, each verified live against the
   real running stack before moving to the next — not just unit-tested in
   isolation. Several real bugs and gaps were found this way rather than by
   inspection (see [Risks](#risks-found-and-mitigated) below).
4. **Consolidated automated testing** (`TESTING.md`) — a `pytest` suite against
   real PostgreSQL, codifying everything verified manually.

This ordering was itself a considered decision, not a default: finishing
requirement discovery for all three scenarios before writing code meant Scenario
B's redaction mechanism could be worked out *before* Scenario A's hash-chain design
was implemented, avoiding a rebuild — see `REQUIREMENTS.md`'s amendment to item 6c,
made during Scenario B's analysis, before any hashing code existed.

**The same four-phase discipline ran a second time, post-submission.** External
code review flagged two gaps — no caller authentication/authorization, and
hardcoded dev secrets with no fail-secure guard. Both went through requirement
clarification (`REQUIREMENTS.md`'s C7–C12, reopening C6 rather than silently
overwriting it — see [Assumptions](#assumptions)), task decomposition (`TASKS.md`'s
C3.1–C3.7 and P3), implementation verified live against the running stack, and
dedicated test coverage (`test_auth.py`, `test_cross_tenant.py`, `test_config.py`)
— the same process, not an ad hoc patch, because the discipline was the point being
demonstrated as much as the code was.

## Artifacts

| Artifact | Contents |
|---|---|
| `REQUIREMENTS.md` | Raw requirements, ambiguities, decisions, rationale, rejected alternatives — the "what and why" |
| `TASKS.md` | Implementation task breakdown, dependency-ordered, each task cited against the decision it implements — the "how," plus live-verification notes per task |
| `ARCHITECTURE.md` | Components, data model, API surface, hash chain design, security model |
| `TESTING.md` | Testing approach, coverage, and what isn't automated |
| `AI_USAGE_LOG.md` | Chronological AI-usage traceability — every design decision and implementation unit, what was proposed, what was accepted/modified/rejected and why |
| `README.md` | Setup instructions, DB role explanation, compliance-reporting usage guide |
| `src/`, `tests/`, `alembic/` | The working prototype itself — 20+ commits reflecting incremental, feature-by-feature development, including the post-submission auth/authz and secrets-guard work |

## Risks found and mitigated

Three concrete examples where a real problem was found during this process (not
merely theorized about) and fixed before it shipped:

- **A concurrency bug in the retention sweep.** Two concurrent sweep calls could
  read the same candidate set before either archived it, producing duplicate audit
  events over the same records. Found while implementing, before it was ever run
  concurrently in practice. Fixed by extending the existing append-lock to cover
  the sweep's read phase. (`TASKS.md`, B2.2)
- **A crash in chain verification.** A plausible, simple attack — replacing a
  record's payload wholesale via direct SQL — crashed the verify endpoint instead
  of reporting tampering. Found by an automated test exercising real Postgres, not
  by manual `curl` testing (which had only exercised value edits). Fixed and locked
  in as a regression test. (`TESTING.md`)
- **A spoofable `actorId` on redaction.** Once auth made `actorId` derivable from
  the authenticated principal (C10), the redact endpoint's request schema still
  silently accepted a caller-supplied `actorId` and just ignored it — a stray or
  attempted-spoof value would vanish with no error, which could mask a caller's
  misunderstanding of who's on record as having performed the redaction. Found
  while writing test coverage for the auth work, not by design review. Fixed with
  `extra="forbid"` on `RedactRequest` and a regression test confirming `422`.

## Trade-offs (consolidated; full rationale in the linked items)

- **Single global hash chain**, not one per resource — stronger tamper-evidence
  (no per-partition deletion blind spot) at the cost of fully serialized writes and
  a non-contiguous export problem, both addressed elsewhere. (`REQUIREMENTS.md` 7a)
- **Per-field salted commitments for `payload`**, not a flat hash — enables
  redaction without destroying tamper-evidence for the rest of a record, at the
  cost of a more complex hash structure than a single blob. (`REQUIREMENTS.md` 3a)
- **Bundle-level signature for export, not a rechained sub-structure** — a
  user-proposed alternative (re-linking exported records into their own chain) was
  seriously evaluated and rejected: it adds no security once the bundle is signed,
  and risks implying stronger guarantees than actually exist. (`REQUIREMENTS.md` 5a)
- **Fail-fast verify**, not a full violation report — matches the requirement's
  literal wording and keeps the common failure case cheap; a "report everything"
  mode is a reasonable future addition, not built without being asked for.
  (`REQUIREMENTS.md` 8b)
- **Static API keys for auth, not JWT bearer tokens or mTLS** — the simplest
  mechanism that still enforces a real caller-identity boundary, at the cost of no
  expiry/rotation/revocation beyond editing config; JWT and mTLS were both
  seriously evaluated and rejected as disproportionate infrastructure (issuer or
  long-lived test tokens; a local CA and TLS-termination changes, respectively) for
  what this exercise needs to demonstrate. (`REQUIREMENTS.md` C7)

## Assumptions

- **No authentication/authorization layer — later reversed.** Originally not
  requested by any of the three scenarios, and explicitly scoped out (C6) on the
  reasoning that Scenario C's regulators never authenticate to this system
  directly (C1). External code review of the initial submission challenged that:
  C1 answered *who consumes a compliance report*, not *who may call the API at
  all* — conflating the two was the actual gap. C6 was reopened rather than
  silently rewritten; C7–C12 document the resulting design (static API keys, four
  roles, `actorId` derived from the authenticated principal for redact/retention,
  per-account resource scoping) and `TASKS.md`'s C3.1–C3.7 the implementation.
  Kept here, not deleted, because the assumption being wrong and then corrected on
  challenge is itself part of the record — see [Plan and process](#plan-and-process).
- **Regulators consume reports via internal compliance staff**, not by
  authenticating to this system directly — the one genuine fork in Scenario C's
  clarification, confirmed explicitly rather than assumed silently. (`REQUIREMENTS.md`
  Scenario C, C1)
- **Dev-only secrets** (DB role passwords, the export signing key seed, API keys)
  are fixed, documented values appropriate for this prototype — every location
  says so explicitly and names what production would need instead (secrets
  manager/KMS, an actual IdP). Also raised in code review: a fail-secure guard
  (`TASKS.md` P3) now refuses to start under `ENVIRONMENT=production` while any of
  the three DB/signing secrets still hold their hardcoded dev value — closing the
  *silently deploying on known values* failure mode specifically, not the
  underlying "no secrets manager" gap, which remains out of scope.
- **`eventType` is caller-defined, not a fixed enum** — this service is a generic
  ingestion sink; enforcing a taxonomy would require every producer to agree on one
  in advance, which isn't asked for. (`REQUIREMENTS.md` 1a)

## Known limitations

Full detail in `ARCHITECTURE.md`'s "Known architectural limitations" and
`TESTING.md`'s "What isn't automated" — not repeated here in full, but the three
most consequential for a live defense:

1. **No external chain anchoring.** The hash chain proves internal consistency, not
   resistance to an attacker willing to rewrite the entire tail consistently.
   Closing this requires periodic external checkpointing — a real production
   feature, out of scope for this prototype, and explicitly named as such rather
   than silently omitted.
2. **Archived records become unreachable via export's filters** once their
   classification fields are nulled by retention — found via live testing, not
   design review, and deliberately documented rather than patched, since fixing it
   would mean either extending export with position-based filters or reopening an
   already-locked retention design. (`REQUIREMENTS.md`, Scenario B item 5e)
3. **Static API keys, not a real identity provider.** Auth (C7) proves "this
   caller holds a configured key," not identity tied to a person/service in any
   external directory — no rotation, expiry, or revocation beyond editing config.
   A real deployment would delegate to an actual IdP (OAuth2/OIDC); the fail-secure
   guard (P3) prevents *deploying on known dev keys*, not this structural gap,
   which stays out of scope by design, same trade-off as the other dev-fixed
   secrets. (`REQUIREMENTS.md` C11; `ARCHITECTURE.md`'s "Known architectural
   limitations")

## What's next

The planned review of documents and test cases produced two rounds of concrete
follow-through so far: production-readiness extensions (HTTP-layer tests, a
write-throughput load test) and, from external code review specifically,
authentication/authorization (C7–C12) and the fail-secure secrets guard (P3) —
both now implemented, tested, and live-verified, not just designed. Remaining
sidelined items are named explicitly, not silently dropped: CI enforcement,
structured logging/observability, full secrets externalization (a real
vault/KMS/IdP — P3 only guards against the worst failure mode of not having
one), a consistent global error-handling schema, and verify-walk-latency-at-scale
testing (`TASKS.md`'s "Production-readiness extensions," `TESTING.md`'s "what
isn't automated"). This summary and the linked documents are kept current as that
work lands, so each claim traces to a specific decision, test, or commit rather
than requiring re-derivation.
