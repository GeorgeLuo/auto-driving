# Proposal: Milestone closeout

| Field | Value |
| --- | --- |
| Milestone | 008 Perception-Memory Workbench Feasibility |
| Frontier | Milestone closeout |
| Proposal branch | `m008/closeout-proposal` |
| Implementation branch | `m008/closeout` |
| Exit criteria | M008-07, M008-08 |
| Review kind | Milestone closeout |

## Review Question

Is milestone 008 complete as a whole—its bounded assessment remains the single
authority, the selected `workbench.image_replay.v1` contract is implemented,
one real image-replay workbench slice is accepted, the CLI and selected page
remain aligned, repeated-run failure/recovery/cleanup stay observation-only,
and residual limits are explicit enough for cumulative PR #167 to receive a
whole-milestone review without hiding deferred product work?

This proposal is ready for implementation only if the closeout implementation
can publish that judgment and prepare the cumulative review surface without
changing runtime behavior, recapturing evidence for freshness, or manually
claiming the terminal plan transition.

## Operator Want

- **Want:** Close M008 as an evidence-backed feasibility milestone for one
  useful, local, observation-only perception-memory workbench journey. After
  whole-milestone integration, return product focus to the separately owned
  M006 frontier or a later product proposal selected from the durable gaps.
- **Reject if:** Closeout treats the accepted POC as support for video or live
  ingestion, arbitrary algorithms, unavailable or isolated plugins, remote
  hosting, movement authority, a Metrics UI redesign, a missing `run_id`
  display, or any other deferred want; or if it hides a missing/contradicted
  accepted criterion behind prose.

## Proposed Contract

### Execution phases (must remain separate)

| Phase | When | Owner | Permitted change |
| --- | --- | --- | --- |
| **0. Whole-milestone readiness review** | While this proposal PR is open and its plan transition has not merged | Reviewer/operator | Audit the M008 objective, completion usage, all criteria, accepted review units, assessment, evidence identity, residuals, and cumulative PR #167. A gap that falsifies an existing criterion keeps this proposal unaccepted; a new product want is a later proposal. |
| **A. Closeout implementation PR** (`m008/closeout` → milestone) | After this proposal is accepted | Implementer | Create `closeout.md`, reconcile the one existing assessment in place, append the M008 completed-ledger entry, reconcile only bounded documentation/navigation, and update draft cumulative PR #167's body and validation notes. Leave M008 Active, M008-07/M008-08 Unmet, risks intact, and the accepted ledger unchanged. |
| **B. Post-merge handoff** | After the closeout implementation PR is squash-merged to a clean milestone branch | `workflow.py complete-implementation --pr <implementation-pr>` | Apply this proposal's Expected Handoff mechanically: mark M008-07 and M008-08 Met, remove only risks whose residual meaning is preserved in `closeout.md`, record the accepted closeout unit, close the plan, clear the frontier, and regenerate `plan.html`. |
| **C. Whole-milestone integration** | After Phase B reaches the milestone tip | Operator/reviewer | Mark #167 ready and review the milestone as a whole. Packet and documentation defects stay on #167. A finding that falsifies an already-Met criterion uses the append-only reject-restore workflow; only an exact-head accepted #167 may merge to `main` and permit tag/branch cleanup. |

Phase 0 is the last inexpensive point to route a required in-milestone unit.
Phase A publishes a judgment but is not terminal. Phase B is the only owner of
the terminal plan facts, and Phase C is not evidence supplied by the closeout
implementation PR.

### Whole-milestone acceptance rules

M008 closes only when all of the following hold:

1. M008-01 through M008-06 are still `Met` at implementation start, with
   accepted PR #174 and #191 and their tracked evidence available at the exact
   identities recorded below.
2. The single assessment at
   `docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md`
   remains authoritative for M008-01, M008-02, and M008-07. Phase A reconciles
   that file in place with the accepted #191 POC judgment and the durable CLI,
   sequence, signal, page-surface, source, plugin, and transport gap
   dispositions. Closeout may update or cite it only as bounded documentation;
   it does not create a second assessment or convert every gap into a backlog
   item.
3. `closeout.md` reconciles the selected sequence contract, accepted
   workbench slice, CLI/page adaptation boundary, failure and recovery cases,
   cleanup and observation-only limits, and residual product decisions.
4. The closeout packet cites the exact accepted implementation and evidence
   identities rather than recapturing live data merely to make dates current.
5. Phase A does not change runtime code, tests of new behavior, evidence
   records, plugin manifests, the M006 milestone, criteria, risks, accepted
   review-unit ledger, or terminal status.
6. Phase B's handoff is the only place that marks M008-07 and M008-08 `Met`,
   removes the corresponding active-plan risks, closes the plan, and records
   the accepted closeout PR.
7. Cumulative PR #167 remains draft until Phase B. It is then reviewed as the
   whole-milestone surface before any merge to `main`; packet defects do not
   become product changes on the cumulative PR.
8. Residuals remain visible, including `run_id` not being shown on the page,
   visual refinement, arbitrary capture/video/live source semantics, isolated
   or model-dependent plugins, M006 dependency, browser/transport timing,
   external Metrics UI capabilities, and the absence of movement authority.

### Criterion judgment basis

| Criterion | Accepted authority | Closeout restatement |
| --- | --- | --- |
| M008-01 | M008 assessment and PR #174 | The relevant M007 perception-memory candidates, CLI seams, and existing pages were bounded and assessed for inputs, signals, side effects, recovery, cleanup, and workbench fit. |
| M008-02 | M008 assessment and PR #174 | `workbench.image_replay.v1` is the one selected sequence: ordered image directory, server-side mapper, Observation, bounded memory, and structured local state. |
| M008-03 | PR #191 and `evidence/replay-workbench-acceptance/` | One loopback server identity stayed available across distinct first, second, failed, and recovered runs, with the browser remaining a presentation client over the shared server-side runner. |
| M008-04 | PR #174 | Existing CLI-launched perception/memory seams and page meanings have one explicit server-owned adaptation boundary; the browser does not define a second sequence. |
| M008-05 | PR #191 and its accepted operator verdict | A real long capture with `classical_regions` produced inspectable overlays and memory, and the operator accepted the delivered display granularity as minimally useful. |
| M008-06 | PR #191 and its accepted operator verdict | Source failure, valid retry, repeated runs, reset, and cleanup stayed observation-only with no worker, simulator, Metrics operation, movement, or recording side effect. |
| M008-07 | The existing M008 assessment plus this closeout judgment | The assessment survives implementation as one evolving authority, with durable CLI, sequence, signal, page-surface, source, plugin, and transport gaps classified as later decisions or no-follow-up observations. |
| M008-08 | Phase A closeout judgment plus Phase B handoff | The selected contract, accepted slice, alignment, safe lifecycle, and residual limits are durable before the plan is closed. |

### Frozen evidence inventory

Closeout cites, but does not replace, these authorities:

| Evidence | Identity used by closeout |
| --- | --- |
| M008 assessment | `docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md`; selected sequence identity `workbench.image_replay.v1`. |
| Workbench implementation | PR #174, squash merge `27b3c343de311e60219abc9b18b4ef293a28b445`; accepted proposal/amendment history #172, #179, #181, and #189 remains in the plan. |
| POC acceptance implementation | PR #191, squash merge `6c2f26a2ce34a5f38431e6b21d1269ea306f526d`; exact evidence head `654649281dc5e732d01c58cdce2935839cabd835`. |
| Operator evidence | `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/`; accepted result commit `e3572d2c875d166efc2d6011384810169e3ce3cb`; Chrome `152.0.7977.76` headed. |
| Session identity | Server `workbench-2d29d6df9d2f`; first `run-af702ee8f0974eabb15bb5bdfa4fff4f`; second `run-3fa4314708804bacbafe9675fff24037`; failed `run-0969dd4fc26d4f3e9ccd04d217c4d156`; recovered `run-614187c4db7e445294420fa5fc4022f2`. |
| Cumulative review | PR #167, targeting `main`, remains draft until Phase B; it must be updated from its stale initial body before Phase C. |

### Residual limits that must survive closeout

Phase A restates these in `closeout.md` without treating them as M008
failures:

| Residual | Durable disposition |
| --- | --- |
| `run_id` is not shown on the page | Enhancement candidate from M008 POC evidence; server state remains the accepted identity surface. A page identity improvement needs its own bounded review if selected. |
| Visual refinement and display granularity | The operator accepted the delivered slice. Further hierarchy, typography, and overlay polish are a product decision, not retroactive acceptance criteria. |
| Video or live ingestion | Deferred until a source contract defines ordering, timestamps, identity, decoding, and lifecycle. |
| Arbitrary algorithm selection and isolated/model-dependent plugins | The manifest catalog exposes unavailable entries and runs only selected ready entrypoints; install, network fetch, and silent fallback remain unsupported. |
| M006 shadow decision surfaces | M006 remains separately owned and its branch/plan/evidence are not edited by M008 closeout. |
| External Metrics UI, browser, loopback transport, and timing | Acceptance covers the recorded local environment and server-owned state only; browser launch, remote/public hosting, and future external contract drift remain bounded assumptions. |
| Movement, vehicle, simulator, and recording authority | The selected journey is observation-only and read-only; no autonomous movement, simulator reconfiguration, worker, or recording claim is made. |
| History persistence and export | Per-run state is process-local. Reset, a new run, successful source validation, or shutdown discards the in-memory history; terminal completion/cancel cleanup resets stage instances but retains terminal history until one of those boundaries. Durable export needs explicit consent and a later proposal. |

### Required Phase A outputs

1. Create `docs/milestones/008-cli-decision-workbench/closeout.md` with the
   whole-milestone judgment, criterion map, evidence identities, residual
   dispositions, validation results, cumulative PR topology, and next-focus
   decision.
2. Reconcile the existing
   `docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md`
   in place: replace stale pre-acceptance status, link the accepted #191
   judgment, and retain one explicit table of durable gaps and their
   later-decision or no-follow-up dispositions. Do not create a second
   assessment or silently turn residuals into implementation work.
3. Append one M008 entry to `docs/milestones/completed.md` in cumulative PR
   #167. The entry must state that Phase C whole-milestone acceptance and the
   `main` merge/tag remain pending; it must not rewrite earlier milestone
   entries.
4. Update `docs/README.md` navigation only if needed to point at the durable
   M008 packet. Do not copy plan status or architecture into the index.
5. Reconcile only factual documentation links or CLI/workbench invocation
   details that have drifted. No runtime, test, plugin, evidence, or M006
   edits are allowed.
6. Update draft PR #167's body with the current objective, completion usage,
   accepted review units #174 and #191, exact validation, residuals, and the
   correct milestone/main topology. Leave it draft.
7. Optionally tighten non-terminal prose in the M008 plan's Closeout section;
   do not change criteria, risks, accepted ledger, current identity, or
   status. The normal terminal transition remains workflow-owned.

### Phase C reject boundary

If whole-milestone review finds only a packet, navigation, or cumulative-body
defect, repair those documents on #167 and re-review the exact head. If a
finding falsifies M008-01 through M008-07 or the accepted workbench behavior,
do not product-fix on closed-plan #167. Use the append-only reject restore to
return the milestone to Active/idle with the closeout criterion Unmet, then
open a new owned proposal. Do not reuse the closeout handoff receipt for a
restore.

## Ownership

| Concern | Owner | Required result |
| --- | --- | --- |
| Whole-milestone judgment | `closeout.md` author and reviewer | One bounded judgment over the objective, completion usage, criteria, evidence, and residuals. |
| Assessment continuity | Existing M008 assessment | One authority remains current; no second assessment or automatic backlog. |
| Accepted workbench contract | PR #174 and its merged implementation | Exact sequence, page/server boundary, and deterministic contract remain cited. |
| Operator acceptance and lifecycle | PR #191 evidence packet | Real source, Chrome inspection, repeated runs, toggles, failure/recovery, and cleanup remain cited. |
| Terminal workflow state | `workflow.py complete-implementation` | Only the reviewed Expected Handoff closes M008 and removes risks. |
| Cumulative integration | PR #167 reviewer/operator | Draft-to-ready transition occurs only after Phase B; whole-milestone review precedes `main`. |
| Cross-milestone focus | M006 owner/operator | M006 remains separate; closeout records the handoff without editing its artifacts. |

## Affected Paths

### Proposal PR only

| Path | Change |
| --- | --- |
| `docs/milestones/008-cli-decision-workbench/proposals/closeout.md` | This reviewed contract. |
| `docs/milestones/008-cli-decision-workbench/plan.md` | Select Milestone closeout in `proposal_in_review`; leave criteria, risks, accepted ledger, status, and terminal facts unchanged. |
| `docs/milestones/008-cli-decision-workbench/plan.html` | Regenerated rendering of the canonical plan. |

### Expected Phase A implementation PR

| Path | Change |
| --- | --- |
| `docs/milestones/008-cli-decision-workbench/closeout.md` | Durable whole-milestone judgment and evidence/residual map. |
| `docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md` | In-place reconciliation of the single assessment with the accepted POC judgment and durable gap dispositions. |
| `docs/milestones/completed.md` | Append M008 closeout packet entry only. |
| `docs/README.md` | Navigation-only change if required. |
| `README.md` or bounded CLI/workbench guide | Factual link/invocation reconciliation only if the audit finds drift. |
| Cumulative PR #167 body | Objective, accepted units, exact validation, residuals, and topology; remain draft until Phase B. |

No Phase A changes are permitted under runtime, CLI, implementation,
perception, memory, plugin, test, or M008 `evidence/` paths, and no M006 file
may change.

### Phase B and Phase C

Phase B changes only the M008 plan and generated `plan.html` through
`complete-implementation` (plus the workflow-owned handoff commit). Phase C
may update the external cumulative PR, merge it to `main`, create the
milestone tag, and clean branches only after exact-head acceptance.

## Adversarial Matrix

| Case | Expected closeout behavior |
| --- | --- |
| Any non-closeout criterion is not `Met` at proposal or implementation start | Reject closeout; retain M008 Active and route the missing criterion to its own review unit. |
| Assessment is missing, duplicated, or replaced by a prose summary | Reject; restore the single assessment as the authority before closeout. |
| Evidence identity, run IDs, server identity, or accepted merge is contradicted | Reject; do not recapture or rewrite the accepted evidence in Phase A. |
| POC screenshot or operator verdict is treated as proof of video, live, remote, or arbitrary-plugin support | Reject the scope expansion; record it as a residual. |
| `run_id` display, visual polish, or another consumer want is requested | Classify as enhancement/P3 residual unless it falsifies the accepted question; do not reopen the milestone by preference. |
| Closeout implementation changes product code, tests, manifests, evidence bytes, or M006 | Reject the PR as scope leakage. |
| Phase A marks M008-07/M008-08 Met, removes risks, closes the plan, or readies #167 | Reject; only Phase B owns terminal facts and Phase C owns cumulative readiness. |
| Phase C finds a packet-only defect | Keep #167 open, repair packet/docs/body only, and re-review the exact head. |
| Phase C falsifies an accepted criterion | Do not merge/tag; use append-only reject restore and open a new proposal from Active/idle. |

## External Assumptions

- The accepted GitHub records for #174, #190, and #191 remain reachable, and
  their merge commits and exact evidence heads continue to identify the same
  reviewed artifacts.
- The local M008 branch continues to contain the accepted implementation and
  evidence without undocumented product changes.
- PR #167 remains the cumulative M008 review surface targeting `main`; its
  draft state is not itself evidence of milestone completion.
- `workflow.py` and the canonical milestone plan remain the authorities for
  review transitions and terminal handoff.
- M006 remains independently active/deferred as recorded in the M008 plan;
  this closeout does not assume that M006 has reached `main`.

## Non-Goals

- No new workbench feature, runtime behavior, plugin, source adapter, or
  browser redesign.
- No live capture, simulator, vehicle, Metrics UI, or browser recapture.
- No video/live ingestion, arbitrary algorithm selection, plugin installation,
  model loading, network fetch, or remote/public hosting.
- No movement, control, simulator reconfiguration, recording, or durable
  replay-history export.
- No edits to M006, prior completed-milestone entries, accepted M008 evidence,
  or accepted review-unit artifacts.
- No terminal status, risk removal, criterion update, cumulative-PR readiness,
  tag, or branch cleanup during proposal or Phase A implementation.

## File Impact

### Proposal-only diff

The proposal branch contains only this proposal, the canonical plan transition,
and its generated HTML. No closeout judgment or completed-ledger row is
created yet.

### Phase A implementation diff

The implementation branch creates the closeout packet, reconciles the existing
single assessment in place, appends the cumulative ledger entry, performs
bounded documentation reconciliation if necessary, and updates PR #167's body.
It does not alter product/runtime code or accepted evidence. The terminal plan
transition is intentionally absent until the post-merge handoff.

## Validation Plan

### Proposal PR

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/008-cli-decision-workbench/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/008-cli-decision-workbench \
  --head-ref m008/closeout-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

Review also checks that the proposal contains one whole-milestone question,
the exact Review Kind `Milestone closeout`, the Phase 0/A/B/C boundary, the
single-assessment rule, the evidence identities, the residual accounting, and
the sole `outcome: close` handoff template.

### Phase A implementation PR

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
python3 docs/milestones/workflow.py validate \
  docs/milestones/008-cli-decision-workbench/plan.md
python3 docs/render_markdown.py --check
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/008-cli-decision-workbench \
  --head-ref m008/closeout \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

Phase A records exact validation at its final head and verifies that all
accepted M008 evidence bytes are unchanged. No live session is required unless
an accepted authority is missing or contradicted; that condition blocks
closeout rather than expanding it.

### Phase B and Phase C

```sh
python3 docs/milestones/workflow.py complete-implementation \
  --plan docs/milestones/008-cli-decision-workbench/plan.md \
  --pr <implementation-pr-number>
python3 docs/milestones/workflow.py status \
  --plan docs/milestones/008-cli-decision-workbench/plan.md
```

Phase B must report M008 `closed`, every criterion `Met`, no current or
remaining frontier, and an accepted closeout ledger row. Only then may PR #167
be marked ready and reviewed as the cumulative milestone.

## Expected Handoff

Post-merge successful closeout implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "close",
  "result": "Accepted",
  "durable_evidence": "M008 closeout judgment in closeout.md; completed.md M008 entry; single bounded assessment retained; accepted PR #174 and PR #191 evidence mapped; selected image-replay contract, operator acceptance, lifecycle safety, residual limits, and cumulative PR #167 preparation recorded in implementation PR #{pr}",
  "criterion_updates": {
    "M008-07": {
      "status": "Met",
      "evidence": "Closeout preserves the single M008 perception-memory assessment and records durable CLI, sequence, signal, page, source, plugin, transport, and visual-gap dispositions in PR #{pr}"
    },
    "M008-08": {
      "status": "Met",
      "evidence": "Closeout confirms the selected workbench.image_replay.v1 contract, accepted real image-replay slice, CLI/page alignment, observation-only recovery and cleanup, residual limits, and cumulative PR #167 topology in PR #{pr}"
    }
  },
  "risk_remove": [
    "The current live page server is associated with a worker rather than an explicit multi-run workbench session",
    "Arbitrary capture or video upload is a candidate interaction, not a proven CLI input",
    "The full M006 shadow decision surfaces are accepted on the M006 branch but are not in `main`",
    "Later visual refinement or a video/live source may still be wanted after this POC acceptance"
  ],
  "risk_upsert": []
}
```

The template is the only reviewed `outcome: close` receipt. `complete-implementation`
materializes the accepted closeout PR number and merge SHA; Phase A must not
predeclare either identity.
