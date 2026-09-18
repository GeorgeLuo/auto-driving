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
`closeout.md`, completed-ledger and navigation updates, and prepare the
cumulative PR **without** pre-claiming terminal plan mutations that the
post-merge mechanical handoff owns.

## Proposed Contract

### Execution phases (must not be collapsed)

Closeout has **three** ordered phases. Mixing them fails CI or invents false
plan history.

| Phase | When | Owner | What may change |
| --- | --- | --- | --- |
| **A. Implementation PR** (`m005/closeout` → milestone) | Before merge | Implementer | Durable judgment docs; navigation; optional plan prose that does **not** touch criteria, risks, ledger, or frontier identity; open/update cumulative PR and record its exact number |
| **B. Post-merge handoff** | After squash-merge of the implementation PR, on a clean milestone branch | `workflow.py complete-implementation --pr <implementation-pr>` | **Only** mechanical terminal plan mutations from this proposal’s Expected Handoff template (M005-13 Met, risk removal, Status `closed`, empty frontiers, accepted-ledger row, history) plus generated `plan.html` |
| **C. Post-handoff operator steps** | After handoff commit is on the milestone tip | Operator | Mark the **exact** cumulative PR ready; whole-milestone review; merge commit to `main`; tag; branch cleanup; 006 activation note as navigation only |

The enforced review-unit contract **prohibits** the implementation PR from
changing exit-criterion statuses, open risks, or the accepted ledger, and
requires the active closeout frontier to remain present while Status is
`Active`. Therefore phase A must not set M005-13=`Met`, must not clear risks,
must not empty frontiers, and must not set Status=`closed`. Those mutations are
**exclusively** phase B via `complete-implementation` applying this template.

### Whole-milestone acceptance rule

Milestone 005 is **complete** only when phases A–C succeed:

1. **M005-01–M005-12 stay Met** on the plan at implementation start (already
   true with accepted implementation units). If any is not Met, stop—open a
   repair unit; do not hide the gap in closeout.
2. **Phase A publishes durable judgment** in `closeout.md` (usefulness, residual
   limits, 006 activate decision, validation snapshot, references) plus
   `completed.md` and `docs/README.md` navigation, without new runtime behavior.
3. **Phase B marks M005-13 Met and closes the milestone** through the reviewed
   Expected Handoff (`outcome: close`), not by hand-editing those cells in the
   implementation PR.
4. **Completion Usage remains honest.** `closeout.md` restates that stage /
   inspect / stream / reset / replay / opt-in record / Pi lifecycle check /
   Chase lifecycle check remain the supported workflows, with idle action for
   the whole milestone. No new operator commands; no movement authority.
5. **Residual risk is explicit.** Phase A restates remaining limits in
   `closeout.md`. Phase B removes the matching open-risk rows named in Expected
   Handoff. Risks must not vanish from the plan without residual restatement.
6. **Cross-milestone handoff is explicit.** Phase A records **activate** for the
   006 pre-plan. Phase C may update navigation after 005 is closed; it must not
   ship 006 product work under `m005/closeout`.
7. **Cumulative PR identity is explicit and not conflated with the
   implementation PR.** Phase A opens or updates the cumulative PR
   (`milestone/005-evidence-memory-foundation` → `main`) and records its **exact
   number** in `closeout.md`. Phase C marks **that** PR ready after handoff.
   `complete-implementation` only verifies the **implementation** PR (`{pr}`);
   it must not be described as proving cumulative readiness.

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
| M005-13 | **Phase A docs + phase B handoff** | Phase A publishes `closeout.md` / completed / nav; phase B sets Met via Expected Handoff |

### What memory representation proved useful

Closeout **must** state, in substance (phase A, in `closeout.md`):

- Retained attributed observation evidence (`thing` / `signal` slots) with
  plugin-safe identity, finite capacity and age, detachable snapshots, explicit
  reset, and opt-in provenance extracts is useful for **continuity across
  frames** while action remains idle.
- Structural same-slot conflict policy (fail-closed invalidation, no semantic
  fusion) is required so recurrence cannot silently change structural meaning.
- Live proof is limited to **stationary / observe-only** paths: Pi lifecycle and
  Chase shadow/max-age with zero unapplied candidate control.

### What remains unverified (residual limits)

Closeout **must** record at least (phase A in `closeout.md`; phase B removes
matching plan risk rows):

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

**Revise only if** phase A finds the pre-plan text factually wrong relative to
closed 004/005 evidence (status line / prerequisite wording). Do not expand 006
scope under 005 closeout. **Do not implement 006 product work** in any phase of
this unit.

### Required outputs by phase

#### Phase A — implementation PR only

1. **`docs/milestones/005-evidence-memory-foundation/closeout.md`** — durable
   judgment: outcome narrative, what proved useful, residual limits, validation
   snapshot, deferred work (006 activate), references, and the **exact cumulative
   PR number** once opened (e.g. “Cumulative PR: #N; mark ready after handoff”).
2. **`docs/milestones/completed.md`** — append-only 005 entry linking plan and
   closeout (same shape as 001–004).
3. **`docs/README.md`** — navigation only: prepare 005 as recently closed / 006
   still pre-plan. Do not claim 006 Active. Wording may say closeout is in
   progress if Status is still Active at PR open; final “closed” navigation must
   remain consistent after phase B.
4. **Plan prose allowed under the review-unit freeze** — optional Milestone
   Decisions row and `## Closeout` body that **point at** `closeout.md` and
   restate the 006 activate decision. **Forbidden in the implementation PR:**
   Exit Criteria status/evidence cells; Open Risks rows; Accepted Review Units
   rows; replacing/emptying Current Frontier or Next-Frontier Candidate;
   header Status=`closed` or Current frontier=`None`.
5. **Cumulative PR process** — open or update
   `milestone/005-evidence-memory-foundation` → `main` as a transitional
   remaining-work delta; record the exact PR number in `closeout.md`. Leave it
   draft or otherwise **not** the readiness gate for phase B. Prefer merge commit
   into `main` later (phase C). Do not squash away milestone history on that
   final merge.
6. **No product/runtime implementation** under `m005/closeout`.

#### Phase B — post-merge `complete-implementation` only

Applies this proposal’s Expected Handoff template with the **implementation** PR
number and merge SHA:

- M005-13 → `Met` with handoff evidence string
- `risk_remove` for the four named open risks (restated as residual in
  `closeout.md`)
- Status → `closed`; frontiers emptied; accepted-ledger row for the
  implementation PR; workflow history update
- Commit **only** `plan.md` + generated `plan.html` on the milestone branch

#### Phase C — post-handoff operator steps (not mechanical handoff evidence)

1. Mark the **exact cumulative PR number recorded in `closeout.md`** ready for
   whole-milestone review (if still draft).
2. Review and merge that cumulative PR into `main` with a merge commit.
3. Tag; remove obsolete milestone/proposal/implementation branches.
4. Activate or revise 006 only as a cross-milestone navigation/status step—not
   as 005 product scope.

### Closeout.md required sections

Use prior closeouts (e.g. 004) as structure guide. Required content blocks:

- Outcome (closed date / whole-milestone result; may note “plan Status closed
  via post-merge handoff” if written before phase B)
- Durable Decisions (memory-as-evidence, idle action, bounds, dual-host,
  provenance, conflict policy pointer)
- What Was Demonstrated (table: claim → evidence path / implementation PR)
- Failures And Residual Limits (matching residual list above)
- Validation (deterministic suite status at closeout tip; cite live proofs
  already landed under #51/#57 and Pi evidence; no new live run required unless
  plan evidence is missing)
- Deferred Work (006 activate; no other competing pre-plan)
- Cumulative PR identity (exact number; “ready after handoff” responsibility)
- References (plan, completed ledger, key **implementation** PRs)

### Validation and non-claims

- Closeout validation is **documentation and plan integrity**, plus a full
  deterministic test run at the closeout implementation tip. Live Pi/Chase
  re-proof is **not** required if M005-09–M005-12 evidence remains tracked.
- Closeout does **not** claim semantic world models, movement authority, or
  restart-durable memory.
- Closeout does **not** re-score max-age design or reopen #64’s conflict matrix.
- Phase B does **not** verify cumulative PR readiness; phase C does, by number.

## Ownership

| Concern | Owner |
| --- | --- |
| Whole-milestone judgment text | Phase A: `closeout.md` |
| M005-13 Met + Status closed + risk clear + empty frontiers | Phase B: `complete-implementation` + Expected Handoff |
| Completed ledger | Phase A: `docs/milestones/completed.md` |
| Navigation | Phase A (+ C touch-up): `docs/README.md` only |
| Cumulative PR open + exact number | Phase A (record in `closeout.md`) |
| Cumulative PR mark-ready / merge to `main` | Phase C (operator; not `{pr}` in handoff) |
| 006 activate decision text | Phase A in `closeout.md`; C for any post-close nav |

## Affected Paths

- Phase A success: `closeout.md` + completed + nav (+ optional allowed plan
  prose); cumulative PR exists with recorded number; frontier still Active
  closeout / `implementation_in_review` until merge.
- Phase B success: plan terminal state from Expected Handoff; only plan.md/html
  in the handoff commit.
- Phase C success: named cumulative PR ready and merged; 006 not implemented
  under 005.
- Block: any non-closeout criterion not Met at implementation start → stop.
- Product code: unchanged in all phases.

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| Phase A ships closeout docs; phase B runs complete-implementation | M005-13 Met; Status closed; ledger row for implementation PR |
| Phase A sets M005-13 Met or Status closed in the implementation PR | Reject (review-unit freeze / validate-pr) |
| Phase A clears Open Risks or ledger rows | Reject |
| Implementer invents new CLI / memory behavior | Out of scope; reject |
| Implementer reopens conflict policy or max-age scoring | Out of scope; reject |
| Phase B claimed without closeout.md | Reject (judgment missing) |
| Residual limits omitted from closeout.md | Reject |
| 006 product work under m005/closeout | Reject |
| Abandon 006 without stronger rationale than this proposal | Reject; frozen **activate** |
| Live re-proof claimed without new evidence paths | Reject; cite tracked evidence |
| Cumulative PR targets something other than main from milestone branch | Reject topology |
| durable_evidence claims “cumulative PR ready” via implementation `{pr}` | Reject; cumulative readiness is phase C by exact number |
| Completion Usage claims default disk history | Reject |
| Risk removed in phase B without residual restatement in closeout.md | Reject |
| Next in-milestone frontier after close | Reject; next remains none |

## External Assumptions

- Accepted **implementation** review units in the plan ledger (#51–#53, #57, #64,
  and baseline through #50) remain the evidence of record for Met criteria.
  Proposal PR **#61** is the accepted **proposal** for #64 (Workflow History /
  accepted-proposal field), not a ledger review unit.
- Tracked evidence under
  `docs/milestones/005-evidence-memory-foundation/evidence/` remains present.
- The 006 pre-plan document already exists and correctly describes immediate
  deferred perception-fitness work after memory.
- Milestone branch `milestone/005-evidence-memory-foundation` is the integration
  tip for the cumulative PR after phase A merges and phase B completes.

## Non-Goals

- New feature implementation, API changes, or test matrices beyond documentation
  integrity / full deterministic suite green.
- Live vehicle re-proof, re-recording Pi/Chase extracts, or Metrics UI changes.
- Reopening M005-08 conflict semantics or max-age scoring design.
- Activating 006 as Active product work or implementing 006 packages in this unit.
- Semantic fusion, object identity, confidence aggregation, non-idle action.
- Rewriting pre-contract mainline history; the cumulative PR may remain a
  transitional delta.
- Teaching `complete-implementation` to verify cumulative PR readiness (out of
  scope for this unit; phase C remains operator-owned).

## File Impact

### Phase A — create

- `docs/milestones/005-evidence-memory-foundation/closeout.md`

### Phase A — modify

- `docs/milestones/completed.md` — append 005 entry.
- `docs/README.md` — navigation only.
- `docs/milestones/005-evidence-memory-foundation/plan.md` — **only** allowed
  prose under the freeze (e.g. Milestone Decisions / Closeout body pointing at
  `closeout.md`). Must **not** change Exit Criteria, Open Risks, Accepted Review
  Units, frontier identity, or Status=`closed`. Workflow state / PR / history
  transitions remain those produced by `start-implementation` and the
  implementation PR link, not terminal close.
- `docs/milestones/005-evidence-memory-foundation/plan.html` — generated only when
  plan.md changes.

### Phase A — process

- Open/update cumulative PR (milestone → `main`); record exact number in
  `closeout.md`. Do not treat “ready” as phase A acceptance evidence.

### Phase B — modify (mechanical only)

- `plan.md` / `plan.html` via `complete-implementation` applying Expected Handoff
  (M005-13 Met, risks removed, Status closed, frontiers emptied, ledger row).

### Phase C — process

- Mark the recorded cumulative PR ready; merge commit to `main`; tag; branch
  cleanup; optional 006 navigation.

### Remove

- None in phases A–B. Obsolete branch cleanup is phase C after cumulative merge.

## Validation Plan

Documentation and integrity only (plus full deterministic suite):

```text
# Proposal independence (this PR) — supply real SHAs from the PR refs
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/005-evidence-memory-foundation \
  --head-ref m005/closeout-proposal \
  --base-sha <base-sha> \
  --head-sha <head-sha>

# After implementation PR (phase A tip), same form with m005/closeout refs
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/005-evidence-memory-foundation \
  --head-ref m005/closeout \
  --base-sha <base-sha> \
  --head-sha <head-sha>

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q
python3 docs/render_markdown.py --check

# After squash-merge of the implementation PR (phase B)
python3 docs/milestones/workflow.py complete-implementation \
  --plan docs/milestones/005-evidence-memory-foundation/plan.md \
  --pr <implementation-pr-number>
```

Acceptance requires:

1. Every adversarial matrix row is addressed by the phase split or a reject rule.
2. Phase A `closeout.md` states usefulness, residual limits, **activate 006**,
   and the **exact cumulative PR number**.
3. Phase A does not pre-claim M005-13 Met, risk removal, empty frontiers, or
   Status closed.
4. Phase B handoff validates and leaves Status closed with all criteria Met.
5. No product paths outside File Impact appear in the implementation PR.
6. Deterministic suite green at the phase A tip.
7. Phase C marks ready / merges the cumulative PR by the number in `closeout.md`
   (not assumed from implementation `{pr}`).

## Expected Handoff

Post-merge **implementation** success template (merge-time implementation PR and
SHA filled by `complete-implementation`; do not predeclare them). This template
is **phase B only**—it must not claim cumulative PR readiness:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "close",
  "result": "Accepted",
  "durable_evidence": "closeout.md; completed.md 005 entry; residual limits restated; 006 activate decision recorded; cumulative PR identity recorded for post-handoff readiness in implementation PR #{pr}",
  "criterion_updates": {
    "M005-13": {
      "status": "Met",
      "evidence": "Closeout published usefulness, residual risk, and 006 activate decision in closeout.md; terminal Met applied by complete-implementation for PR #{pr}"
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

### Sequence after this proposal merges

1. **Accept proposal** (milestone branch):
   ```text
   python3 docs/milestones/workflow.py accept-proposal \
     --plan docs/milestones/005-evidence-memory-foundation/plan.md \
     --pr <this-proposal-pr-number>
   ```
   Commit the resulting plan/HTML on the milestone branch.
2. **Start implementation only**:
   ```text
   python3 docs/milestones/workflow.py start-implementation \
     --plan docs/milestones/005-evidence-memory-foundation/plan.md \
     --branch m005/closeout
   ```
3. **Phase A:** implement only the phase A File Impact (docs + allowed plan
   prose + open/record cumulative PR number). Open implementation PR. Do not
   start 006. Do not set terminal criterion/risk/status in the PR.
4. Squash-merge the implementation PR into the milestone branch.
5. **Phase B:** on a clean local milestone branch:
   ```text
   python3 docs/milestones/workflow.py complete-implementation \
     --plan docs/milestones/005-evidence-memory-foundation/plan.md \
     --pr <implementation-pr-number>
   ```
   This applies the Expected Handoff above (M005-13 Met, risks cleared, Status
   closed). It does **not** mark the cumulative PR ready.
6. **Phase C:** mark ready and merge the cumulative PR named in `closeout.md`;
   tag; cleanup branches; 006 navigation only.
