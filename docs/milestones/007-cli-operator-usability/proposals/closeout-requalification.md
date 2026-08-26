# Proposal: Milestone closeout requalification

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Milestone closeout requalification |
| Proposal branch | `m007/closeout-requalification-proposal` |
| Implementation branch | `m007/closeout-requalification` |
| Exit criterion | M007-06 |
| Review kind | Milestone closeout |
| Controlling prior contract | [Accepted closeout proposal #143](closeout.md) |

## Review Question

After accepted repairs #146, #154, and #155, is the prior M007 closeout
contract still sufficient when its retained packet, append-only withdrawal
history, and cumulative PR #81 are reconciled to those repairs?

The whole-milestone closeout question is unchanged. Proposal #143 controls
every term not explicitly changed below. This proposal exists because #143 was
consumed by implementation #144, Phase C rejected cumulative PR #81, and the
reviewed restore returned M007 to Active/idle. The prior proposal's reject path
requires a new proposal from idle; accepted proposal history is not rewritten
or reused as a new implementation receipt.

## Operator Want

- **Want:** Review only the post-rejection delta, then re-run the already
  accepted closeout procedure against the repaired milestone.
- **Reject if:** The implementation would need new closeout policy beyond this
  delta, or any original Phase C finding still reproduces.

## Proposed Contract

### Inherited unchanged

The following sections of [proposal #143](closeout.md) remain controlling:

- the Phase A closeout implementation, Phase B mechanical handoff, and Phase C
  cumulative integration boundaries;
- the whole-milestone acceptance rule except where the retained packet and
  repair ancestry are updated below;
- frozen evidence identities and the rule that accepted live evidence is
  cited rather than recaptured;
- US-01 through US-10, `M007-LIVE-*`, issue, capability-group, and residual
  accounting;
- evidence rendering, unsupported-claim boundaries, and M006 separation;
- packet-only repair versus criterion-falsifying reject handling; and
- the append-only restore procedure if a later Phase C review again rejects a
  `Met` criterion.

Reviewers do not need to re-accept those settled terms. This proposal overrides
only the following four implementation facts.

### Delta 1: accepted repair basis

The three Phase C findings must be rechecked at their accepted owners:

| Finding | Accepted repair | Required result |
| --- | --- | --- |
| Primary commands leaked malformed timeout `ValueError` | PR #146; reviewed head `787f9f967c6b0ed276036943a5122e11c4a424be`; milestone merge `f6d221c0c602e648efc4bdd355c909a9bca3fa12` | `vehicles status`, `vehicles automation run`, and `vehicles update perception` reject `0`, negative, `nan`, `-nan`, `inf`, `+inf`, and `-inf` before dispatch with exit 2, stable human/JSON input errors, no traceback or side effect; finite positive and default timeout behavior remains unchanged |
| Staged PiRacer inspection hid reachable live state/view | PR #154; reviewed head `d6120956a5a14ccbbb754b89379e79e6f8ccf4d4`; milestone merge `1b08ff596df9b2a9ad23ef1d2947ccf85cb0f551` | `vehicles info perception --id piracer` preserves valid offline staged `active.json`, enriches it with reachable live observation and local-view state, reports staged/live availability consistently in human and JSON output, and treats live outage as unavailable live state without staging, worker, control, or input actions |
| Chase accepted decoded dimension and MIME/format mismatches | PR #155; reviewed head `23982845948a61346953d285aba2eaeb5de34418`; milestone merge `ff6c00f2ac98a40f2aab9cfa198fc9bb3d0da386` | Decoded dimensions, raster format, data-URL MIME, and declared content type agree before write/publish; invalid cases fail `capture_image_invalid` before publication; supported PNG/JPEG/GIF/WEBP captures and optional evaluator-reference independence remain intact |

M007-03 therefore cites #84 plus #155; M007-04 cites #84 plus #146; the
PiRacer inspection portion of M007-06 cites #154. M007-06 remains `Partial`
until the Phase B handoff.

### Delta 2: retained closeout packet

Phase A updates the retained `closeout.md` rather than creating a new packet.
It must add rejected cumulative PR #81 head
`ee2e3056f77bee9a4511877829eb9c46b52d0aa2` and restore head
`9f758d9927d8b870b1d3d2219441fd7410d64b47`, cite the existing withdrawal and
restore receipts, and cite #146/#154/#155,
update repaired criterion evidence, and record exact current validation. It
must not relabel #144 as accepted cumulative closure or change accepted
evidence bytes.

### Delta 3: append-only completed ledger

The existing M007 packet and following `cumulative review withdrawn` section
remain unchanged and in order. Phase A appends a short
`007 CLI Operator Usability — cumulative review requalified` section naming
#146/#154/#155 and stating that whole-milestone acceptance is still pending
PR #81. It must not claim a `main` merge or `milestone-007` tag.

### Delta 4: cumulative PR #81 refresh

Phase A updates draft PR #81 to:

- list accepted units #84, #88, #100, #107, #122, #138, #146, #154, and #155;
- retain rejected #144 and the withdrawal as history;
- remove the three repaired findings from unresolved risks;
- record the current closeout identity and exact validation; and
- remain draft until Phase B closes the plan and Phase C starts.

Everything else in the #143 PR-body, documentation, validation, and handoff
contract remains unchanged.

## Ownership

| Delta | Owner |
| --- | --- |
| Repair ancestry and focused recheck | Accepted owners in PRs #146, #154, and #155 |
| Retained judgment | `docs/milestones/007-cli-operator-usability/closeout.md` |
| Append-only history | `docs/milestones/completed.md` |
| Workflow state and terminal close | M007 `plan.md`, generated `plan.html`, and `workflow.py complete-implementation` |
| Cumulative review surface | Draft PR #81 body in Phase A; readiness/review in Phase C |

## Affected Paths

Proposal PR #156 changes only:

- `docs/milestones/007-cli-operator-usability/proposals/closeout-requalification.md`;
- M007 `plan.md`; and
- generated M007 `plan.html`.

Expected Phase A paths remain those accepted by #143, with these refinements:

- update the existing M007 `closeout.md`;
- append, never rewrite, `docs/milestones/completed.md`;
- update draft PR #81's body; and
- change root/docs/operator-guide prose only if factual drift is proven.

No product, test, evidence, prior proposal, or M006 path is affected.

## Adversarial Matrix

The full #143 matrix remains controlling. Review #156 only against this delta:

| Bypass | Required response |
| --- | --- |
| Any original timeout, PiRacer, or image-envelope reproduction still violates its required result | Reject closeout and route a new owned repair unit |
| #146, #154, or #155 lacks exact accepted ancestry | Reject requalification |
| Phase A rewrites/deletes the original M007 packet or withdrawal | Reject; append-only history is required |
| `closeout.md` or PR #81 omits a repair or still lists it unresolved | Reject stale requalification |
| Phase A marks M007-06 `Met`, clears risks, closes the plan, or marks #81 ready | Reject phase crossing; Phase B/C own those facts |
| Phase A changes product, tests, evidence bytes, prior proposals, or M006 | Reject scope leakage |
| Requalification promotes historical evidence, deferred work, or unsupported capability | Apply the unchanged #143 reject rule |
| A later Phase C finds another criterion-falsifying defect | Apply the unchanged append-only reject restore; do not reuse the success handoff |

## External Assumptions

- GitHub retains the review and merge identities for #81, #146, #154, and
  #155; plan ledger entries remain local ancestry authority.
- Accepted evidence and the residual/capability registries used by #143 remain
  unchanged. Missing or conflicting authority blocks closeout.
- Issue and remote M006 state may change; Phase A refreshes their cited state
  without changing their artifacts or the inherited disposition rules.
- No new live simulator, browser, PiRacer, movement, or evidence recapture is
  needed. A contradicted accepted authority blocks closeout instead of
  expanding this unit.

All other external assumptions in #143 remain controlling.

## Non-Goals

- Re-reviewing unchanged #143 terms.
- Rewriting #143, #144, the PR #81 verdict, or withdrawal history.
- Changing product behavior, tests, evidence, residual dispositions, or M006.
- Marking, merging, or tagging PR #81 before its inherited Phase B/C gates.
- Adding a new closeout output beyond the four deltas above.

## Evidence Rendering

- Derived HTML: skip.
- Skip reason: the delta mints no sealed machine-readable signal; accepted
  records remain authority and M007 `plan.html` is generated from `plan.md`.

## File Impact

| Phase | Impact |
| --- | --- |
| Proposal | This proposal, M007 `plan.md`, generated `plan.html` only |
| Phase A | Inherited #143 documentation/PR-body paths, refined to update retained `closeout.md` and append a requalification ledger section |
| Phase B | M007 `plan.md` and generated `plan.html` through `complete-implementation` |
| Phase C | PR #81 state/review/merge/tag or the inherited packet-repair/reject restore |

## Validation Plan

Proposal validation is unchanged except for the new branch and artifact:

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/closeout-requalification-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

Phase A reuses every #143 offline integrity, evidence, parser/help, workflow,
render, and full-suite check, plus focused public-door tests for the three
repairs:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.cli.vehicles.test_timeout_input \
  tests.cli.perception.test_commands \
  tests.implementations.vehicle.test_chase_frame_identity
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
```

It must also verify exact repair receipts/ancestry, the original Phase C
inputs, append-only ledger order, retained-packet reconciliation including the
rejection and restore identities above, actual PR #81 body, and the unchanged
#143 evidence authorities. The focused commands must exercise the public-door
`--timeout-s` cases and no-dispatch assertions from #146, staged/local-plus-
reachable/unavailable-live human-and-JSON cases from #154, and decoded
dimension/raster/MIME/content-type mismatch, supported-raster, optional-
reference, and pre-publication cases from #155. Record exact final counts,
skips, identities, and non-claims. Phase B/C validation remains unchanged from
#143.

## Expected Handoff

The success transition keeps the #143 shape and updates only its durable
evidence for requalification:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "close",
  "result": "Accepted",
  "durable_evidence": "Requalified M007 closeout judgment in closeout.md; original packet and withdrawal preserved; completed.md requalification appended; accepted repairs #146, #154, and #155 reconciled; inherited #143 evidence, residuals, and next-focus decision verified; cumulative PR #81 prepared for fresh whole-milestone review in implementation PR #{pr}",
  "criterion_updates": {
    "M007-06": {
      "status": "Met",
      "evidence": "Requalified closeout preserves rejected-review history, confirms accepted repairs #146/#154/#155, and revalidates the inherited primary-journey, documentation, evidence, US-01 through US-10, capability, and residual accounting in PR #{pr}"
    }
  },
  "risk_remove": [
    "Metrics UI may evolve independently of this repository",
    "A browser tab can be visibly open before its Play WebSocket role is registered",
    "Evaluator reference data is useful for scoring but is not sensor input",
    "Browser opening is platform-dependent",
    "Running every CLI leaf can be unsafe or environment-dependent",
    "Confirmed exploratory product defects from PR #88 remain deferred without owners",
    "Cited sequence passed status is historical, not continuous HEAD verification",
    "Capability dispositions are historical to the sealed M007-07 report"
  ],
  "risk_upsert": []
}
```

After proposal acceptance, merge, and `accept-proposal`, start
`m007/closeout-requalification` and perform only the delta plus inherited #143
Phase A work. The inherited Phase B/C sequence then applies unchanged.

## Review Kind

**Milestone closeout** — review only whether the accepted #143 closeout
contract remains sufficient after the rejected cumulative review, three
accepted repairs, retained packet, and append-only withdrawal history.
