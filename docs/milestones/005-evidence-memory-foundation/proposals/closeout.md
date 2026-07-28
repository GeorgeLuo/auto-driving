# Proposal: Milestone closeout

Milestone: 005 Evidence Memory Foundation  
Frontier: Milestone closeout  
Proposal branch: `m005/closeout-proposal`  
Implementation branch: `m005/closeout`  
Exit criterion: M005-13  

## Review Question

Is milestone 005 complete as a whole—every exit criterion Met, completion
usage supported, residual risk stated—and should the 006 pre-plan be activated,
revised, or abandoned?

This proposal is ready for implementation only if an implementer can write
`closeout.md`, ledger/navigation updates, and the cumulative milestone PR
judgment without inventing new product policy or reopening closed frontiers.

## Proposed Contract

### Whole-milestone acceptance rule

Milestone 005 is **complete** only when all of the following hold at the
closeout implementation merge:

1. **Every exit criterion M005-01–M005-13 is `Met`** in the plan Exit Criteria
   table. M005-01–M005-12 are already `Met` with accepted review-unit evidence.
   Closeout marks **M005-13** `Met` by publishing the durable judgment surfaces
   below—not by new runtime behavior.
2. **Completion Usage remains honest.** The plan’s Completion Usage table is the
   operator surface set. Closeout must restate that stage / inspect / stream /
   reset / replay / opt-in record / Pi lifecycle check / Chase lifecycle check
   remain the supported workflows, with idle action policy unchanged for the
   whole milestone. Closeout must not invent new operator commands or claim
   movement authority.
3. **No concealed unfinished work.** Closeout must not mark criteria Met by
   narrative alone when the ledger row evidence is missing. If any criterion
   other than M005-13 is not already `Met` at implementation start, stop and
   open a repair unit—do not hide the gap inside closeout.
4. **Residual risk is explicit.** Open risks that remain true after closeout are
   either removed from the plan risk table and restated as durable residual
   limits in `closeout.md`, or kept only if they still block a later milestone
   (they must not silently disappear).
5. **Cross-milestone handoff is explicit.** Closeout records one decision for
   the 006 pre-plan: **activate**, **revise**, or **abandon**. Activating 006 is
   a status/navigation change after 005 closes; it is **not** 005 scope and must
   not ship 006 product work under `m005/closeout`.

### Criterion judgment basis (do not re-prove)

Closeout **cites** accepted evidence; it does not re-run live vehicles or rewrite
accepted contracts.

| ID | Already Met via | Closeout restatement requirement |
| --- | --- | --- |
| M005-01 | Typed `MemorySnapshot` / activation | Mention stable decision-cycle memory types |
| M005-02 | Stage meaning + idle action | Mention observation/memory/patterns/projections/action ownership; action idle for entire 005 |
| M005-03 | #52 bounds/detach/identity; #53 record bounds | Mention finite capacity/age, detach, reset, provenance, isolated failure |
| M005-04 | `BoundedEvidenceLedger` | Mention one packaged implementation without world-model claims |
| M005-05 | Chase + Donkey host wiring | Mention same activation/lifecycle on both hosts without privileged map inputs |
| M005-06 | Stage/inspect/stream/reset/replay CLI | Mention Automa operator surface completeness |
| M005-07 | Defaults write nothing; #53 ceilings | Mention opt-in bounded recording only |
| M005-08 | Recurrence/dropout/expiry/capacity/reset/failure/replay plus **#64** conflict policy | Mention deterministic matrix including same-slot structural conflict (`bounded_evidence_structural_v1`) |
| M005-09 | Pi lifecycle evidence; #51 shadow; #57 max-age | Mention live Pi present/dropout/expiry/reset and guided Chase max-age extract |
| M005-10 | Atomic evaluator path / #51 | Mention observe-only rewrite; built-in model retains movement authority |
| M005-11 | Design/wiring + #51 | Mention evaluator-only shadow state absent from candidate/memory |
| M005-12 | Physical + Chase provenance extracts | Mention retained image-space evidence traces to source frames; retained ≠ current |
| M005-13 | **This unit** | Publish `closeout.md` + completed ledger + residual risk + 006 decision |

### What memory representation proved useful

Closeout **must** state, in substance:

- Retained attributed observation evidence (`thing` / `signal` slots) with
  plugin-safe identity, finite capacity and age, detachable snapshots, explicit
  reset, and opt-in provenance extracts is useful for **continuity across
  frames** while action remains idle.
- Structural same-slot conflict policy (fail-closed invalidation, no semantic
  fusion) is required so recurrence cannot silently change structural meaning.
- Live proof is limited to **stationary / observe-only** paths: Pi lifecycle and
  Chase shadow/max-age with zero unapplied candidate control.

### What remains unverified (residual limits)

Closeout **must** record at least:

| Residual | Why it remains |
| --- | --- |
| Process-local memory only | Restart continuity was an explicit non-goal |
| Chase live probe process identity | Host command inspection can fail closed; not a security boundary |
| Metrics UI atomic capture dependency | Chase evidence quality depends on sibling capture contract |
| Transitional cumulative PR shape | Pre-contract M005 work targeted `main`; cumulative PR is a remaining-work delta from the milestone branch, not a full rewrite of history |
| Physical perception quality for movement | 004 residual (side misses / clear-floor false positives) was not re-solved in 005 |
| Semantic fusion, identity, non-idle action | Explicit 005 non-goals |

### Decision on milestone 006

**Decision frozen by this proposal: activate** the existing pre-plan
`docs/milestones/006-decision-facing-perception-readiness/` after 005 closes.

Rationale:

- 005 delivered the memory substrate 004 deferred; it did not measure
  decision-facing perception fitness for non-idle control.
- The 006 pre-plan already freezes that next question (fitness measures; at most
  one upgrade attempt; explicit reject-and-keep-control allowed; action remains
  idle until 006 gates).
- No evidence from 005 justifies abandoning 006 or replacing it with a different
  milestone.

**Revise only if** closeout implementation finds the pre-plan text factually
wrong relative to closed 004/005 evidence (status line / prerequisite wording).
Do not expand 006 scope under 005 closeout. **Do not activate 006 product work**
while 005 closeout is open.

### Required closeout outputs

Implementation of this proposal produces exactly:

1. **`docs/milestones/005-evidence-memory-foundation/closeout.md`** — durable
   judgment: outcome, what proved useful, residual limits, validation snapshot,
   deferred/next work (006 activate), references to plan and accepted units.
2. **Plan terminal state** — M005-13 `Met`; Status `closed`; current/next
   frontiers empty per `outcome: close` handoff; Open Risks emptied of items
   restated as residual in closeout; Milestone Decisions row recording closeout
   and the 006 activation decision.
3. **`docs/milestones/completed.md`** — append-only 005 entry linking plan and
   closeout (same shape as 001–004 entries).
4. **`docs/README.md`** — navigation only: 005 moves to recently closed; 006
   remains the immediate pre-plan (or is noted as next-to-activate after the
   cumulative merge process completes—do not claim 006 is Active until its own
   activation step).
5. **Cumulative milestone PR** — open or update the long-lived PR from
   `milestone/005-evidence-memory-foundation` → `main` as a transitional
   remaining-work delta; mark it ready for whole-milestone review. Prefer merge
   commit into `main` after acceptance (per planning contract). Do not squash
   away milestone history on that final merge.
6. **No product/runtime implementation** under `m005/closeout` except docs and
   plan/HTML generation required by the workflow.

### Closeout.md required sections

Use prior closeouts (e.g. 004) as structure guide. Required content blocks:

- Outcome (closed date; one-paragraph whole-milestone result)
- Durable Decisions (memory-as-evidence, idle action, bounds, dual-host, provenance, conflict policy pointer)
- What Was Demonstrated (table: claim → evidence path / PR)
- Failures And Residual Limits (table or bullets matching residual list above)
- Validation (deterministic suite status at closeout tip; cite that live proofs
  already landed under #51/#57 and Pi evidence; no new live run required unless
  the plan evidence is missing)
- Deferred Work (006 activate; no other competing pre-plan)
- References (plan, completed ledger, key PRs)

### Validation and non-claims

- Closeout validation is **documentation and plan integrity**, plus a full
  deterministic test run at the closeout tip to ensure the cumulative delta does
  not regress. Live Pi/Chase re-proof is **not** required for closeout if
  M005-09–M005-12 evidence remains tracked and referenced.
- Closeout does **not** claim semantic world models, movement authority, or
  restart-durable memory.
- Closeout does **not** re-score max-age design or reopen #64’s conflict matrix.

## Ownership

| Concern | Owner |
| --- | --- |
| Whole-milestone judgment text | `docs/milestones/005-evidence-memory-foundation/closeout.md` |
| Exit criterion M005-13 + terminal plan state | `plan.md` / generated `plan.html` via workflow handoff |
| Completed ledger | `docs/milestones/completed.md` |
| Navigation | `docs/README.md` only |
| Cumulative PR readiness / merge topology | Milestone branch → `main` PR (process) |
| 006 activation note | Closeout + docs navigation; not 006 product code |

## Affected Paths

- Success: all criteria Met; closeout.md published; milestone Status `closed`;
  completed ledger updated; cumulative PR ready; 006 decision = activate.
- Block: any non-closeout criterion not Met at implementation start → stop;
  do not force close.
- Residual: open risks restated in closeout.md; plan risk table cleared of
  restated items.
- Cross-milestone: 006 remains pre-plan until its own activation; no 006
  implementation under this unit.
- Product code: unchanged.

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| All M005-01–M005-12 already Met; closeout docs land | M005-13 Met; Status closed; handoff `outcome: close` succeeds |
| Implementer invents new CLI / memory behavior in closeout PR | Out of scope; reject |
| Implementer reopens conflict policy or max-age scoring | Out of scope; reject |
| Closeout marks M005-13 Met without closeout.md | Reject |
| Closeout omits residual process-local / Metrics UI / cumulative-delta limits | Reject |
| Closeout activates 006 product work (code/tests under 006 scope) | Reject; docs/status only |
| Closeout abandons 006 without decision-log rationale stronger than this proposal | Reject; proposal freezes **activate** |
| Closeout claims live re-proof without new evidence paths | Reject; cite existing tracked evidence |
| Cumulative PR targets something other than `main` from milestone branch | Reject topology |
| Completion Usage workflows contradicted (e.g. claims default disk history) | Reject |
| Open risk silently deleted without residual restatement | Reject |
| Attempt to promote a next in-milestone frontier after close | Reject; next remains none |

## External Assumptions

- Accepted review units #51–#53, #57, #61, #64 (and baseline through #50) remain
  the evidence of record for Met criteria.
- Tracked evidence under
  `docs/milestones/005-evidence-memory-foundation/evidence/` remains present.
- The 006 pre-plan document already exists and correctly describes immediate
  deferred perception-fitness work after memory.
- Milestone branch `milestone/005-evidence-memory-foundation` is the integration
  tip for the cumulative PR after this unit merges.

## Non-Goals

- New feature implementation, API changes, or test matrices beyond documentation
  integrity / full deterministic suite green.
- Live vehicle re-proof, re-recording Pi/Chase extracts, or Metrics UI changes.
- Reopening M005-08 conflict semantics or max-age scoring design.
- Activating 006 as Active product work or implementing 006 packages.
- Semantic fusion, object identity, confidence aggregation, non-idle action.
- Rewriting pre-contract mainline history; the cumulative PR may remain a
  transitional delta.

## File Impact

### Create

- `docs/milestones/005-evidence-memory-foundation/closeout.md` — durable
  whole-milestone judgment (sections above).

### Modify

- `docs/milestones/005-evidence-memory-foundation/plan.md` — M005-13 Met;
  Status closed; frontiers emptied via close handoff; risks/decisions updates;
  Closeout section completed.
- `docs/milestones/005-evidence-memory-foundation/plan.html` — generated only.
- `docs/milestones/completed.md` — append 005 entry.
- `docs/README.md` — navigation only (005 closed; 006 still pre-plan / next).

### Process (not necessarily path-local)

- Cumulative PR: `milestone/005-evidence-memory-foundation` → `main`, ready for
  whole-milestone review after closeout content merges to the milestone branch.

### Remove

- None required. Obsolete branch cleanup happens **after** cumulative merge per
  planning contract, not as a silent PR side effect.

## Validation Plan

Documentation and integrity only (plus full deterministic suite):

```text
# Proposal independence (this PR)
python3 docs/milestones/workflow.py validate-pr \
  --plan docs/milestones/005-evidence-memory-foundation/plan.md \
  --base milestone/005-evidence-memory-foundation \
  --head m005/closeout-proposal

# After implementation (closeout PR, not this proposal)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
python3 docs/render_markdown.py --check
```

Acceptance requires:

1. Every adversarial matrix row is addressed by closeout content or explicitly
   out of scope with a reject rule above.
2. `closeout.md` states usefulness, residual limits, and **activate 006**.
3. Plan validates closed with all criteria Met.
4. No product paths outside File Impact appear in the implementation PR.
5. Deterministic suite green at closeout tip.

## Expected Handoff

Post-merge implementation success template (merge-time PR/SHA filled by
workflow; do not predeclare them):

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "close",
  "result": "Accepted",
  "durable_evidence": "closeout.md; completed.md 005 entry; cumulative milestone PR ready; residual limits restated; 006 activate decision recorded in PR #{pr}",
  "criterion_updates": {
    "M005-13": {
      "status": "Met",
      "evidence": "Closeout published usefulness, residual risk, and 006 activate decision; cumulative milestone PR ready in PR #{pr}"
    }
  },
  "risk_remove": [
    "Process identity for Chase live probe relies on host command inspection",
    "Memory is process-local by default",
    "Metrics UI atomic capture remains an external dependency for Chase evidence",
    "Historical 005 review units targeted `main` rather than a milestone integration branch"
  ],
  "risk_upsert": []
}
```

After this proposal merges:

1. Record acceptance (`ready_for_implementation`) with:
   ```text
   python3 docs/milestones/workflow.py accept-proposal \
     --plan docs/milestones/005-evidence-memory-foundation/plan.md \
     --pr <this-proposal-pr-number>
   ```
2. Start **only** the implementation branch from the post-acceptance milestone tip:
   ```text
   python3 docs/milestones/workflow.py start-implementation \
     --plan docs/milestones/005-evidence-memory-foundation/plan.md \
     --branch m005/closeout
   ```
3. Implement **only** this proposal (docs + plan terminal state + cumulative PR
   readiness). Do not start 006 implementation.
4. On merge of the closeout implementation PR, complete handoff with
   `outcome: close` using this template so the milestone Status becomes
   `closed` and M005-13 is Met.
