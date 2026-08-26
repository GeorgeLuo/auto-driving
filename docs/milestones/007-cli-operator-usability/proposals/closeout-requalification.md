# Proposal: Milestone closeout requalification

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Milestone closeout requalification |
| Proposal branch | `m007/closeout-requalification-proposal` |
| Implementation branch | `m007/closeout-requalification` |
| Exit criterion | M007-06 |
| Review kind | Milestone closeout |

## Review Question

After the three Phase C product-boundary findings have been repaired through
accepted review units #146, #154, and #155, is milestone 007 complete as a
whole, with its retained closeout packet reconciled to those repairs, every
exit criterion backed by accepted evidence, residual limits preserved, and
cumulative PR #81 ready for a fresh whole-milestone review?

This is a new closeout review unit, not a repair commit on the rejected
closeout implementation. It preserves the accepted #143 proposal, the retained
#144 packet, the rejected PR #81 verdict, and the append-only withdrawal record
as history. It asks whether the post-repair milestone can now publish a new
closeout judgment without hiding or rewriting that history.

## Operator Want

- **Want:** Requalify M007 for whole-milestone acceptance now that every Phase C
  product finding has an accepted owning repair, then hand cumulative PR #81
  to an independent exact-head review.
- **Reject if:** Any original Phase C reproduction still succeeds, any repair
  lacks accepted ancestry, or closeout hides the rejection/withdrawal history,
  promotes residual work, or claims terminal state before the mechanical
  handoff.

## Requalification Basis

Phase C reviewed cumulative head
`ee2e3056f77bee9a4511877829eb9c46b52d0aa2` and rejected whole-milestone
acceptance. The accepted reject path restored M007 to Active/idle at
`9f758d9927d8b870b1d3d2219441fd7410d64b47`, retained the closeout packet, and
appended a withdrawal section instead of deleting history.

The three blocking findings now have separate accepted owners:

| Finding | Accepted repair | Required requalification proof |
| --- | --- | --- |
| Malformed timeout values escaped the shared CLI error boundary | PR #146, reviewed head `787f9f967c6b0ed276036943a5122e11c4a424be`, milestone merge `f6d221c0c602e648efc4bdd355c909a9bca3fa12` | Zero, negative, NaN, and infinity fail before command work with stable exit 2 human/machine errors and no traceback across all affected primary consumers |
| Staged PiRacer inspection suppressed reachable live observation/view state | PR #154, reviewed head `d6120956a5a14ccbbb754b89379e79e6f8ccf4d4`, milestone merge `1b08ff596df9b2a9ad23ef1d2947ccf85cb0f551` | Local staged inspection remains usable offline and is enriched when PiRacer is reachable, while a live outage does not invalidate local inspection |
| Chase accepted dimensions and MIME/format declarations that disagreed with decoded image bytes | PR #155, reviewed head `23982845948a61346953d285aba2eaeb5de34418`, milestone merge `ff6c00f2ac98a40f2aab9cfa198fc9bb3d0da386` | Decoded dimensions, raster format, data-URL MIME, and any declared content type agree before write/publish; original mismatch cases fail as `capture_image_invalid` |

The proposal review rechecks that these repairs are present at the current
milestone head and that the original failure classes are closed. It does not
reopen their accepted contracts or add new product scope.

## Proposed Contract

### Phase separation

| Phase | When | Owner | Permitted change |
| --- | --- | --- | --- |
| **0. Requalification review** | While this proposal PR is open | Reviewer/operator | Audit the repaired Phase C cases, all exit criteria, accepted evidence, retained packet, withdrawal history, residuals, and proposed closeout procedure. A criterion-falsifying gap blocks proposal acceptance and must be routed as a new owned product/evidence unit from Active M007. |
| **A. Closeout implementation** | Only after this proposal has an accepted exact-head receipt, is merged, and is recorded by `accept-proposal` | Implementer on `m007/closeout-requalification` | Reconcile the retained `closeout.md`, append a requalification record to `completed.md`, reconcile bounded documentation, update draft PR #81's body and final validation, and leave product/evidence bytes unchanged. |
| **B. Terminal handoff** | After the Phase A implementation PR is accepted and merged | `workflow.py complete-implementation` | Mechanically mark M007-06 `Met`, remove active risks after their residual meaning is durable, record the accepted closeout unit, set Status `closed`, empty current/work order, and regenerate `plan.html`. |
| **C. Cumulative integration** | Only after the Phase B commit reaches the milestone tip | Operator/reviewer | Refresh and mark PR #81 ready, review the milestone as a whole, and merge/tag only after exact-head acceptance. Packet defects remain on #81; a criterion-falsifying product/evidence finding uses the established append-only reject restore. |

Phase A must leave M007 `Active`, the closeout frontier in
`implementation_in_review`, M007-06 `Partial`, all eight plan risks present,
and PR #81 draft. Phase B owns terminal facts. Phase C is not evidence supplied
by Phase A or B and can still reject the cumulative milestone.

### Acceptance predicates

M007 may proceed from this proposal only when all of the following hold:

1. **Repair ancestry and receipts are exact.** PRs #146, #154, and #155 are
   merged into the milestone branch from the reviewed heads recorded above,
   and the plan's accepted-unit ledger names their owning questions and
   evidence.
2. **The prior Phase C failure classes are closed.** Focused deterministic
   tests cover each timeout input, local-plus-live PiRacer inspection, decoded
   image dimensions, raster/MIME/content-type agreement, supported raster
   formats, optional evaluator-reference independence, and rejection before
   image publication. The three original image-envelope examples must reject.
3. **Previously accepted criteria remain true.** M007-01 through M007-05 and
   M007-07 through M007-10 remain `Met`; closeout cannot repair or reinterpret
   them. M007-06 remains `Partial` until the terminal handoff.
4. **The retained packet is reconciled, not silently reused.** `closeout.md`
   must identify the rejected cumulative review, the three accepted repair
   units, the new requalification unit, current validation, and the continued
   distinction among Phase A judgment, Phase B closure, and Phase C
   whole-milestone acceptance.
5. **Completed-ledger history stays append-only.** The original M007 packet and
   following `cumulative review withdrawn` section remain byte-for-byte in
   order. Phase A appends a new requalification section that names repairs
   #146/#154/#155 and states that PR #81 acceptance is still pending; it does
   not rewrite either earlier section or claim a mainline merge/tag.
6. **Accepted evidence remains authority.** Closeout verifies committed
   identities and bytes offline, cites historical live evidence at its
   recorded repositories/commits, and does not recapture or regenerate
   evidence merely for recency.
7. **Sequence, capability, and residual accounting remains complete.** All
   US-01 through US-10 rows, five `M007-LIVE-*` rows, issues #89 through #91,
   ten capability groups, and the historical/coverage/platform limits remain
   visible without promotion.
8. **PR #81 is a truthful review surface.** Phase A replaces its stale status,
   resolved-risk list, accepted-unit list, exact head/validation, and repair
   ledger summary while keeping it draft. Phase C alone marks it ready.
9. **Cross-milestone work stays separate.** The current M006 remote plan may be
   cited as the next operator focus, but M007 closeout does not edit, activate,
   merge, or implement M006.

### Criterion authority after repair

| Criterion | Accepted authority used by closeout |
| --- | --- |
| M007-01 and M007-02 | PR #84 passive Chase journey, state model, exact recovery, and durable operator guide |
| M007-03 | PR #84 sensor/reference separation plus PR #155 decoded image-envelope closure |
| M007-04 | PR #84 bounded journey behavior plus PR #146 timeout input/error-envelope consistency |
| M007-05 | PR #88 tracked live acceptance at its recorded auto-driving and Metrics UI commits |
| M007-06 | PR #154 PiRacer inspection compatibility plus the new Phase A closeout judgment and Phase B handoff |
| M007-07 | PR #107 frozen named-context branch-aware journey-coverage report and verifier |
| M007-08 | PR #122 complete 49-leaf and US-01 through US-10 registry |
| M007-09 | PR #138 capability-disposition record covering all 93 candidates in ten groups |
| M007-10 | PR #100 representative machine-first/HITL scenario-continuity evidence |

### Frozen evidence and non-claims

Phase A must preserve and revalidate these existing authorities:

| Evidence | Required closeout statement |
| --- | --- |
| Durable operator guide | The documented six-command passive Chase workflow remains the supported primary journey and grants no movement authority |
| Live CLI acceptance | Historical `pass` at auto-driving `caf335797b71df1323736a2054934b7c211418b0` and Metrics UI `722e070fdc9f4ee89d13f947bf3996e62dcb2783`, with lag 15 within bound 24, observation-only authority, no default recording, and cleanup |
| Scenario continuity | Historical `pass` for the required offline-perception, live-config-swap, and memory-lifecycle families with machine-first/HITL confirmation, restoration, and cleanup |
| Journey coverage | PR #107 head `fda10c6b6f7fe98c7904d0b9bbfa1bc45c6b671b`; report digest `51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789`; informational only |
| CLI surface audit | 49 parser leaves accounted for; US dispositions remain two `passed`, seven `deferred`, one `blocked` |
| Capability disposition | 93 candidate members assigned across ten groups; nine `retain`, one `expose`, zero `remove`; historical and not implementation authorization |

The accepted live artifacts are not a continuous guarantee for later Metrics
UI, browser, PiRacer, or repository heads. Coverage never proves correctness
or dead code. Closeout does not claim autonomous movement safety, non-idle
authority, public remote-view hosting, or hardware/hazardous-leaf execution.

### Retained packet reconciliation

Phase A updates the existing `closeout.md` rather than replacing its evidence
tables. At minimum it must:

- add a requalification history section naming the rejected PR #81 head,
  restore head, and accepted repair units #146/#154/#155;
- update the exit-criterion/evidence map for repaired M007-03, M007-04, and the
  PiRacer portion of M007-06;
- state that M007-06 remains `Partial` and PR #81 remains draft during Phase A;
- include #146, #154, and #155 in the accepted-unit and reference accounting;
- replace stale validation counts and identities with exact Phase A results;
- preserve every US, live-residual, capability, and durable-limit table unless
  its committed source authority proves a correction is required; and
- cite this proposal/implementation as requalification rather than relabeling
  rejected #144 as accepted whole-milestone closure.

Phase A appends, after the withdrawal section in `completed.md`, a new heading
`007 CLI Operator Usability — cumulative review requalified`. It records the
three accepted repairs and the renewed closeout packet, but says whole-
milestone acceptance remains pending PR #81. Only the eventual mainline merge
and tag make that packet durable closure.

### Cumulative PR #81 contract

The Phase A implementation updates the PR body using the cumulative milestone
template. The body must contain:

- current objective and links to completion usage, exit criteria, closeout,
  and the durable operator guide;
- accepted implementation/evidence units #84, #88, #100, #107, #122, #138,
  #146, #154, and #155;
- rejected closeout unit #144 and its withdrawal as historical context rather
  than an accepted closeout result;
- current residuals and unsupported claims without listing the three repaired
  Phase C findings as unresolved;
- exact final Phase A validation, explicit historical-live non-claims, current
  base/head topology, and an updated repair-cycle ledger; and
- an explicit statement that PR #81 remains draft until Phase B closes the
  plan and Phase C begins.

No proposal PR update to #81 is required. Phase A owns the body update because
its validation and implementation identity must be final before cumulative
review.

### Phase C disposition

After Phase B, an exact-head cumulative review has three outcomes:

- **Accept:** mark #81 ready, obtain a decisive exact-head whole-milestone
  review, merge it to `main` with a merge commit, tag `milestone-007`, then
  clean obsolete M007 branches and resume M006 separately.
- **Packet-only repair:** keep #81 open and repair only `closeout.md`, the new
  unmerged requalification ledger section, bounded documentation, or #81's
  body; record the repair cycle and re-review the new head.
- **Criterion-falsifying reject:** do not merge/tag or hide product work on the
  closed plan. Revert only the Phase B terminal plan commit, append another
  withdrawal section, apply the already-reviewed exceptional `advance`
  restore shape, leave M007 Active/idle with M007-06 non-Met and risks restored,
  then route a new owned proposal.

The success `Expected Handoff` below must never be reused as the Phase C reject
receipt.

## Ownership

| Boundary | Owner |
| --- | --- |
| Requalified whole-milestone judgment and retained packet | `docs/milestones/007-cli-operator-usability/closeout.md` |
| Append-only packet/withdrawal/requalification history | `docs/milestones/completed.md` |
| Durable navigation and operator procedure | `docs/README.md`, root `README.md`, and `docs/reference/cli-simulator-perception-journey.md`, only when factual drift is proven |
| Current workflow and terminal mutation | M007 `plan.md`, `plan.html`, and `workflow.py complete-implementation` |
| Cumulative review surface | PR #81 body in Phase A; readiness and whole-milestone review in Phase C |
| Repair authority | Accepted units #146, #154, and #155; no new enforcement owner in closeout |
| Next focus | Canonical remote M006 plan, cited only; activation/implementation remains separate |

## Affected Paths

- `docs/milestones/007-cli-operator-usability/proposals/closeout-requalification.md`
  owns this proposal contract.
- `docs/milestones/007-cli-operator-usability/plan.md` and generated
  `plan.html` own the proposal transition and later workflow-owned Phase A/B
  state changes.
- `docs/milestones/007-cli-operator-usability/closeout.md` owns the requalified
  whole-milestone judgment during Phase A.
- `docs/milestones/completed.md` owns append-only packet, withdrawal, and
  requalification history.
- `docs/README.md`, root `README.md`, and
  `docs/reference/cli-simulator-perception-journey.md` may change in Phase A
  only when a factual documentation audit proves drift.
- PR #81's body is the external cumulative review surface updated in Phase A;
  its readiness, merge, and tag remain Phase C operations.

No path below `autonomy/`, `cli/`, `implementations/`, `tests/`, any M007
`evidence/` or `tools/` directory, or the M006 milestone is affected by the
proposal or expected Phase A implementation.

## Adversarial Matrix

| Attempted bypass or failure | Required response |
| --- | --- |
| Any original Phase C timeout, PiRacer, or image-envelope reproduction still fails its required outcome | Reject closeout; route a new owned repair unit instead of changing product code here |
| A repair PR is absent from milestone ancestry or lacks an exact-head accepted receipt | Reject requalification |
| Closeout cites the repaired plan row without running the focused public-door regression | Reject; plan prose is not validation |
| Phase A sets M007-06 `Met`, removes risks, sets Status `closed`, or empties current | Reject; those facts belong to Phase B |
| Phase A changes CLI, runtime, implementation, tests, evidence bytes, or M006 | Reject as scope leakage |
| Existing M007 packet or withdrawal text is deleted, reordered, or rewritten | Reject; append-only history is required |
| New completed-ledger text claims PR #81 merged or tag `milestone-007` exists | Reject false external state |
| `closeout.md` omits #146/#154/#155 or still describes their findings as unresolved | Reject stale requalification |
| PR #81 body still identifies the restored `9f758d9` state or lists repaired findings as unresolved | Reject Phase A completeness |
| PR #81 is marked ready before Phase B terminal handoff | Reject phase crossing |
| Historical live, sequence, reachability, or capability evidence is described as current HEAD proof | Reject |
| Accepted evidence is regenerated merely for a current timestamp | Reject; preserve bytes and verify existing authority |
| Any US or `M007-LIVE-*` row, issue #89/#90/#91, capability group, or durable residual disappears | Reject incomplete accounting |
| `expose` is called implemented, `retain` is called journey-covered, or coverage authorizes deletion | Reject overclaim |
| Hardware, movement, destructive, or external leaves are run merely to improve closeout evidence | Reject unsafe/unnecessary validation |
| Closeout claims PiRacer movement parity, non-idle safety, or public remote hosting | Reject unsupported scope |
| Closeout edits or promotes M006 based on its cited state | Reject cross-milestone mutation |
| Phase C discovers a packet-only defect | Keep #81 open; repair only the packet/docs/body and re-review exact head |
| Phase C discovers a product/evidence defect that falsifies a Met criterion | Revert terminal handoff, append withdrawal, restore Active/idle with non-Met M007-06, then open a new proposal |

## External Assumptions

- GitHub retains exact PR/review/merge metadata for #81, #146, #154, and #155;
  workflow acceptance remains the local authority for recorded ancestry.
- The accepted live/evidence packages remain byte-identical to their recorded
  review units. Missing or conflicting authority blocks closeout.
- Issues #89, #90, and #91 are currently open; #139 and #141 are closed. Phase A
  refreshes these statuses rather than copying this proposal blindly.
- The remote M006 milestone is currently Active at
  `6da43547d16195ccc70b4804b8229cc5d2bed057`, with `Cross-environment shadow
  proposal evidence` ready for proposal and zero applied-control policy. Phase
  A refreshes that external state; a change updates only the closeout's next-
  focus statement, never M006 artifacts.
- Deterministic fixtures and existing accepted live artifacts are sufficient.
  No live simulator, browser, PiRacer, or movement run is required unless a
  cited authority is missing or contradicted, which blocks rather than expands
  closeout.

## Non-Goals

- Rewriting or deleting accepted proposal #143, implementation #144, the PR
  #81 changes-requested receipt, or either existing completed-ledger section.
- Repairing product code, tests, or evidence inside closeout.
- Reopening accepted repair contracts or adding new timeout, PiRacer, Chase,
  or CLI behavior.
- Promoting deferred sequences, live findings, capability candidates, or open
  issues into completed work.
- Recapturing live evidence, refreshing sealed reachability, or using coverage
  as correctness/removal authority.
- Marking PR #81 ready, merging/tagging it, or cleaning branches before Phase B
  and independent Phase C review.
- Editing or implementing the M006 milestone.

## Evidence Rendering

- Derived HTML: skip.
- Skip reason: this closeout unit mints no new sealed machine-readable signal;
  it cites accepted records and maintains Markdown judgment/history. The
  canonical milestone `plan.html` remains generated from `plan.md`.

## File Impact

### Proposal PR only

- `docs/milestones/007-cli-operator-usability/proposals/closeout-requalification.md`
- `docs/milestones/007-cli-operator-usability/plan.md`
- `docs/milestones/007-cli-operator-usability/plan.html` (generated)

No product, test, evidence, prior proposal, closeout packet, completed ledger,
reference, or GitHub PR-body change belongs in this proposal PR.

### Expected Phase A implementation

- `docs/milestones/007-cli-operator-usability/closeout.md`
- `docs/milestones/completed.md` (append-only requalification section)
- `docs/README.md`, root `README.md`, and
  `docs/reference/cli-simulator-perception-journey.md` only if factual drift is
  demonstrated
- M007 `plan.md` and generated `plan.html` only for normal
  start-implementation state and optional non-terminal closeout prose
- PR #81 body as an external GitHub update, kept draft

Phase A may not change `autonomy/`, `cli/`, `implementations/`, `tests/`, M007
`evidence/` or `tools/`, prior proposal artifacts, or any M006 path.

### Phase B and Phase C

- Phase B: M007 `plan.md` and generated `plan.html` only through
  `complete-implementation`.
- Phase C accept: PR #81 readiness/review/merge, tag, cleanup, then separate
  M006 work.
- Phase C packet repair: bounded packet/docs/ledger-candidate/PR-body changes
  on #81.
- Phase C criterion reject: terminal-plan revert, append-only withdrawal, and
  exceptional Active/idle restore receipt.

## Validation Plan

### Proposal PR

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

Review verifies proposal-only paths, one closeout question, exact repair
ancestry, append-only rejected-closeout history, Phase A/B/C separation,
complete residual accounting, and absence of terminal or product changes.

### Phase A implementation

Run the focused repair doors first:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.cli.vehicles.test_timeout_input \
  tests.cli.perception.test_commands \
  tests.implementations.vehicle.test_chase_frame_identity
```

The focused run must cover every original Phase C input and assert the final
human/machine or write/publication outcome, not merely helper behavior. Then
run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
python3 docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/validate_audit.py
python3 docs/milestones/007-cli-operator-usability/tools/capability-disposition/capability_disposition.py validate
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/closeout-requalification \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

The implementation must also resolve `pull/107/head` to
`fda10c6b6f7fe98c7904d0b9bbfa1bc45c6b671b`, verify its report under that
accepted ancestry, and byte-compare it with the report carried at the closeout
head. It verifies current issue states, accepted repair receipts/merge ancestry,
all cited evidence paths/digests, parser/help agreement, unchanged accepted
evidence bytes, append-only completed-ledger order, and PR #81's actual body.

Record exact commands, counts, skips, identities, and failures at the final
implementation head. Accepted live artifacts are cited, not rerun. A missing
or contradicted authority blocks closeout instead of silently widening it.

### Phase B and Phase C

After accepted implementation merge:

```sh
python3 docs/milestones/workflow.py complete-implementation \
  --plan docs/milestones/007-cli-operator-usability/plan.md \
  --pr <implementation-pr-number>
python3 docs/milestones/workflow.py status \
  --plan docs/milestones/007-cli-operator-usability/plan.md
```

Status must report M007 closed, every criterion `Met`, no current or remaining
frontier, and the requalification implementation in the accepted ledger. Only
then may Phase C mark PR #81 ready and request whole-milestone review.

## Expected Handoff

Post-merge successful closeout implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "close",
  "result": "Accepted",
  "durable_evidence": "Requalified M007 closeout judgment in closeout.md; original packet and withdrawal history preserved; completed.md requalification appended; Phase C repairs #146, #154, and #155 reconciled; accepted journey, live, coverage, audit, and capability evidence mapped; residuals and next-focus decision recorded; cumulative PR #81 prepared for fresh whole-milestone review in implementation PR #{pr}",
  "criterion_updates": {
    "M007-06": {
      "status": "Met",
      "evidence": "Requalified closeout preserves the rejected cumulative-review history, confirms accepted repairs #146/#154/#155, reconciles the primary journey and durable CLI documentation, maps accepted coverage/full-leaf/capability evidence, preserves accountable US-01 through US-10 and residual limits, and prepares PR #81 for independent whole-milestone review in PR #{pr}"
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

### Sequence after this proposal merges

1. Obtain an independent exact-head accepted contract review for this proposal
   and merge it into the milestone branch.
2. Run `workflow.py accept-proposal` for the proposal PR and commit the recorded
   acceptance transition.
3. Start `m007/closeout-requalification`; implement Phase A only and keep PR
   #81 draft.
4. Obtain exact-head implementation acceptance, merge the implementation PR,
   then run and commit `complete-implementation` for Phase B.
5. Refresh PR #81 to the terminal head, mark it ready, and conduct a new Phase
   C whole-milestone review.
6. Follow the accept, packet-repair, or criterion-reject disposition above.
   Only accepted Phase C permits mainline merge, tag, branch cleanup, and
   separate M006 resumption.

## Review Kind

**Milestone closeout** — a fresh whole-milestone acceptance judgment over the
post-Phase-C-repair implementation, retained packet/withdrawal history,
completion usage, accepted evidence, durable documentation, residual limits,
cumulative integration, and next cross-milestone focus.
