# Final Engineering Summary

## Objective

A tamper-evident audit log service built across three scenarios: a core append-only,
hash-chained log (Scenario A); retention and field-level redaction extensions
(Scenario B); and a compliance-reporting capability for an intentionally
under-specified requirement (Scenario C). Built AI-assisted, engineer-led — every
design decision in this repository was proposed, reasoned about, and explicitly
confirmed before implementation, not generated and accepted wholesale.

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

## Artifacts

| Artifact | Contents |
|---|---|
| `REQUIREMENTS.md` | Raw requirements, ambiguities, decisions, rationale, rejected alternatives — the "what and why" |
| `TASKS.md` | Implementation task breakdown, dependency-ordered, each task cited against the decision it implements — the "how," plus live-verification notes per task |
| `ARCHITECTURE.md` | Components, data model, API surface, hash chain design, security model |
| `TESTING.md` | Testing approach, coverage, and what isn't automated |
| `AI_USAGE_LOG.md` | Chronological AI-usage traceability — every design decision and implementation unit, what was proposed, what was accepted/modified/rejected and why |
| `README.md` | Setup instructions, DB role explanation, compliance-reporting usage guide |
| `src/`, `tests/`, `alembic/` | The working prototype itself — 14+ commits reflecting incremental, feature-by-feature development |

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

## Assumptions

- **No authentication/authorization layer.** Not requested by any of the three
  scenarios; explicitly scoped out with the reasoning recorded at the point each
  scenario could plausibly have pulled it back into scope. (`REQUIREMENTS.md` 2a
  Scenario A; C6 Scenario C)
- **Regulators consume reports via internal compliance staff**, not by
  authenticating to this system directly — the one genuine fork in Scenario C's
  clarification, confirmed explicitly rather than assumed silently. (`REQUIREMENTS.md`
  Scenario C, C1)
- **Dev-only secrets** (DB role passwords, the export signing key seed) are fixed,
  documented values appropriate for this prototype — every location says so
  explicitly and names what production would need instead (secrets manager/KMS).
- **`eventType` is caller-defined, not a fixed enum** — this service is a generic
  ingestion sink; enforcing a taxonomy would require every producer to agree on one
  in advance, which isn't asked for. (`REQUIREMENTS.md` 1a)

## Known limitations

Full detail in `ARCHITECTURE.md`'s "Known architectural limitations" and
`TESTING.md`'s "What isn't automated" — not repeated here in full, but the two most
consequential for a live defense:

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

## What's next

Per the user's stated plan: a thorough review of the documents and test cases,
after which further tests or design updates may follow. This summary and the linked
documents are written to make that review efficient — each claim traces to a
specific decision, test, or commit rather than requiring re-derivation.
