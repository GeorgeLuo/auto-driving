# Proposal: Milestone closeout

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Milestone closeout |
| Proposal branch | `m007/closeout-proposal` |
| Implementation branch | `m007/closeout` |
| Exit criterion | M007-06 |
| Review kind | Milestone closeout |

## Review Question

Is milestone 007 complete as a whole—its supported CLI journeys remain
documented, every exit criterion is backed by accepted evidence, every #88
US-01 through US-10 sequence and every unreached capability has an accountable
disposition, residual limits are explicit, and the cumulative milestone is
ready for whole-milestone review without hiding follow-on product work?

This proposal is ready for implementation only if an implementer can publish
the durable closeout judgment, reconcile documentation and residual ownership,
and prepare cumulative PR #81 without changing product behavior or manually
pre-claiming terminal plan mutations owned by the post-merge handoff.

## Operator Want

- **Want:** Close M007 as an evidence-backed CLI operator-usability milestone
  and return operator focus to the already-active M006 frontier after the M007
  cumulative merge.
- **Reject if:** Closeout treats a deferred sequence, open product issue,
  historical coverage capture, capability candidate, unsupported environment,
  or unexecuted hazardous leaf as completed behavior.

## Proposed Contract

### Execution phases (must remain separate)

| Phase | When | Owner | Permitted change |
| --- | --- | --- | --- |
| **0. Whole-milestone closeout-readiness review** | While proposal PR #143 is open and its plan transition has not merged | Reviewer/operator | Review the M007 objective, completion usage, every exit criterion, cumulative implementation, accepted evidence, durable documentation, and residuals. Record blockers on #143. A blocking product/evidence gap keeps #143 unaccepted; close it before selecting a new work node from the still-Active milestone branch. |
| **A. Closeout implementation PR** (`m007/closeout` → milestone) | After this proposal is accepted | Implementer | Durable closeout judgment, completed ledger, navigation and bounded CLI-document reconciliation, cumulative PR #81 body/final-validation preparation, and optional non-terminal plan prose |
| **B. Post-merge handoff** | After the implementation PR is squash-merged to a clean milestone branch | `workflow.py complete-implementation --pr <implementation-pr>` | Mechanical M007-06 `Met`, risk removal, Status `closed`, empty frontier, accepted-ledger row, workflow history, and generated `plan.html` from this proposal's Expected Handoff |
| **C. Whole-milestone integration** | After the handoff commit reaches the milestone tip | Operator/reviewer | Mark cumulative PR #81 ready and review M007 as a whole. Findings against the existing M007 completion contract stay on #81 as repair cycles; only an exact-head accepted #81 may merge to `main`, be tagged `milestone-007`, and permit cleanup and M006 resumption. |

Phase 0 is the last canonical point at which a newly discovered blocker can
route to a new in-milestone node without reopening a closed plan: because #143
has not merged, the milestone branch itself remains Active and idle. Phase A
must leave M007 `Active`, M007-06 `Unmet`, the closeout frontier present, the
open-risk table intact, and the accepted-review-unit ledger unchanged. Phase B
alone applies the reviewed terminal facts. Phase C is not evidence supplied by
the implementation PR and cannot be claimed by `complete-implementation`.

### Whole-milestone acceptance rule

M007 closes only when all of the following hold:

1. **Phase 0 finds no node-worthy blocker.** Proposal review audits the whole
   milestone before accepting #143. A closeout-contract defect is repaired on
   #143. A product or evidence gap required by an existing criterion keeps
   #143 unmerged; close the proposal before opening a new owned proposal from
   the unchanged Active/idle milestone branch. A new independent want is P3 or
   later residual work, not a closeout blocker.
2. **Previously accepted criteria remain Met.** M007-01 through M007-05 and
   M007-07 through M007-10 must still be `Met` at implementation start. If any
   accepted artifact or criterion is missing or contradicted, stop; closeout
   cannot conceal it or repair product behavior.
3. **Phase A publishes the judgment.** `closeout.md` must reconcile the
   supported completion usage, accepted review units, evidence identities,
   durable CLI documentation, sequence registry, capability dispositions,
   open issues, and residual limits without product or runtime changes.
   The M007 entry in append-only `completed.md` is part of this cumulative
   closeout diff, before #81 is marked ready or reviewed.
4. **Phase B closes the plan mechanically.** The reviewed `outcome: close`
   handoff marks M007-06 `Met`, removes plan risks only after their residual
   meaning is preserved in `closeout.md`, records the accepted closeout PR,
   sets Status `closed`, and empties the in-milestone frontier.
5. **Phase C reviews the cumulative milestone.** Cumulative PR #81 must be
   updated from its stale initial body, marked ready only after Phase B, and
   reviewed as the whole-milestone surface before merge to `main`. A finding
   that falsifies #81's existing completion contract stays in #81, enters its
   Repair Cycle Ledger, and is repaired and re-reviewed at the exact new head.
   A new independent want is nonblocking residual work. Do not invent a new
   frontier or exceptional handoff from the closed plan; a genuine need to
   change milestone scope or canonical recovery mechanics stops #81 and first
   requires a separately reviewed process change.
6. **Accepted evidence is cited, not re-authored.** Closeout performs offline
   integrity checks and full deterministic validation. It does not recapture
   live Chase/Pi evidence merely to refresh dates, redefine accepted verdicts,
   or turn historical coverage into a HEAD claim.
   M007-07 integrity is checked under the exact merged PR #107 head that owns
   its source ancestry, then the verified report is byte-compared with the
   report carried by the closeout head. It is not reinterpreted through the
   current-head freshness gate after the implementation was squash-merged.
7. **Residual work remains visible.** Open issues #89, #90, and #91; the five
   owned `M007-LIVE-*` residuals; deferred/blocked US rows; and the
   `cli-operator-surfaces` expose candidate remain explicit follow-on work.
8. **Cross-milestone work stays separate.** After M007 reaches `main`, operator
   focus returns to the already-active M006 `Cross-environment shadow proposal
   evidence` frontier. M007 closeout does not edit, promote, implement, or
   re-contract M006.

### Criterion judgment basis (do not re-prove)

| Criteria | Accepted authority | Required closeout restatement |
| --- | --- | --- |
| M007-01–M007-04 | PR #84 and `docs/reference/cli-simulator-perception-journey.md` | Supported passive Chase workflow, layer model, recovery ownership, bounded timeout behavior, and unchanged simulator/control state |
| M007-05 | PR #88 and tracked `evidence/live-cli-acceptance/` package | Pass at the recorded auto-driving/Metrics UI commits; bounded-stale frame correlation, observation-only authority, no default recording, and cleanup; not a current-environment guarantee |
| M007-10 | PR #100 and tracked `evidence/cli-scenario-continuity/` package | Three required families passed with machine-first/HITL confirmation, restore and cleanup; optional families and larger experiment features remain residual |
| M007-07 | PR #107 and `evidence/cli-journey-coverage/report.json` | Reproducible named-context branch-aware coverage with exact capture identities and no correctness, dead-code, or percentage-gate claim |
| M007-08 | PR #122 and `evidence/cli-surface-audit/` | Forty-nine parser leaves accounted for; all US-01 through US-10 rows defined and dispositioned; deferred/blocked rows remain honest and owned |
| M007-09 | PR #138 and `evidence/capability-disposition/record.json` | Ninety-three candidate source members assigned to ten owned groups; nine `retain`, one `expose`, zero `remove`; disposition is historical and not implementation authorization |
| M007-06 | Phase A closeout judgment plus Phase B handoff | Documentation, accepted evidence, sequence and capability accounting, residual limits, cumulative PR identity, and next-focus decision are durable before terminal Met |

### Frozen evidence inventory for closeout

Closeout cites these committed authorities and records their identities in the
durable judgment. It does not replace them with prose summaries as authority.

| Evidence | Frozen fact used at closeout |
| --- | --- |
| Durable operator guide | `docs/reference/cli-simulator-perception-journey.md`, linked from root `README.md` |
| Live CLI acceptance | `result: pass`; auto-driving `caf335797b71df1323736a2054934b7c211418b0`; Metrics UI `722e070fdc9f4ee89d13f947bf3996e62dcb2783`; bounded-stale lag 15 within bound 24; no acceptance findings |
| Scenario continuity | `result: pass` at behavior head `37b7393fe759f1597860a30d8c10ca5692f1c0cc`; required offline-perception, live-config-swap, and memory-lifecycle families passed; named HITL, cleanup, restore, and finalizer passed |
| Journey coverage | PR #107 head `fda10c6b6f7fe98c7904d0b9bbfa1bc45c6b671b`; report digest `51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789`; source commit `7931fa9a995af5626fabef818f9e28b98c73e299`; 34 commands/contexts, 37 shards, 63 represented owned files; behavioral verdict not evaluated |
| CLI surface audit | 49 leaves: 32 action, 10 meta, 7 alias; sequence dispositions 2 passed, 7 deferred, 1 blocked; zero unclassified leaves; help drift `ok` |
| Capability disposition | Record digest `81ce4993fe8624bbc818bcad7142dafb78e2be1ef6c45a6115ae535a51477e6f`; 96 sealed source members, 93 candidates, 93 assigned, zero residual membership errors; 10 groups |

If Phase A finds any committed authority missing, malformed, digest-invalid, or
in conflict with the accepted plan ledger, it stops. A closeout prose claim
cannot repair evidence drift.

### Sequence and live-residual accounting

`closeout.md` must state all current registry outcomes without promoting them:

- US-01 and US-02 are historically `passed`.
- US-03 through US-09 are `deferred`, each with the owner and unlock condition
  already committed in M007-08.
- US-10 is `blocked` on a labeled physical corpus and remains owned by
  `physical-perception-lab`.
- `M007-LIVE-001` through `M007-LIVE-005` remain deferred with the five
  committed owners. The plan risk's phrase “without owners” is superseded by
  the M007-08 residual registry; the underlying product gaps remain residual.
- Issue #89 remains the collision-resistant recorded-run identity bug. Issues
  #90 and #91 remain the separately scoped same-frame experiment and
  transactional live-trial features. None is required to make an accepted M007
  criterion true.
- Closed issues #139 and #141 require no further product work: #139 was closed
  as superseded by PR #138's accepted dashboard design, and #141 was completed
  by the integrated command-display adjunct.

### Capability accounting

The closeout judgment must enumerate all ten groups, not only totals:

| Group | Disposition | Owner |
| --- | --- | --- |
| `autonomy-decision-runtime` | `retain` | `repo_path:autonomy` |
| `autonomy-perception-plugins` | `retain` | `repo_path:autonomy/perception` |
| `autonomy-vehicle-boundary` | `retain` | `repo_path:autonomy/vehicle` |
| `cli-operator-surfaces` | `expose` | `repo_path:cli/automa_cli` |
| `implementation-memory` | `retain` | `repo_path:implementations/memory` |
| `implementation-operations` | `retain` | `repo_path:implementations/operations` |
| `implementation-package-boundaries` | `retain` | `repo_path:implementations` |
| `implementation-perception` | `retain` | `repo_path:implementations/perception` |
| `implementation-runtime` | `retain` | `repo_path:implementations/runtime` |
| `implementation-vehicle` | `retain` | `repo_path:implementations/vehicle` |

`retain` means a named non-CLI/dynamic/platform owner remains. `expose` means a
later proposal may define a CLI change. Zero groups are `remove`. Closeout does
not create a leaf, refactor retained code, delete code, or treat source-member
counts as authorization.

### Residual limits that must survive closeout

Phase A restates at least the following limits in `closeout.md`; Phase B may
then remove the matching active-plan risk rows without erasing them:

| Residual | Durable closeout statement |
| --- | --- |
| External Metrics UI drift | Live acceptance proves the recorded sibling commit only; future capture-contract drift requires a new bounded live unit |
| Frontend registration timing | Browser presence and Play WebSocket readiness can differ; bounded readiness and exact recovery mitigate but do not eliminate external timing variance |
| Evaluator reference boundary | Sensor-only observation remains valid without evaluator reference; reference-dependent evidence stays fail-closed |
| Browser launch and remote view | View health is authoritative; OS browser launch is non-fatal/platform-dependent; public or non-loopback remote hosting is unsupported |
| PiRacer and hazardous leaves | M007 did not execute hardware, movement, destructive, or external-state leaves merely for coverage; US-10 remains blocked on labeled physical data |
| Applied movement and non-idle control | Every M007 live path is observation-only; no autonomous movement safety or non-idle authority is claimed |
| Deferred product usability | #89 and `M007-LIVE-001..005` remain owned follow-on defects; #90/#91 remain larger feature candidates |
| Historical sequence evidence | Cited `passed` sequences are historical, not continuous verification of later HEADs |
| Historical reachability | M007-07 and M007-09 are sealed historical measurements; later product changes require recapture before new reachability claims |
| Coverage interpretation | Executed code is not necessarily correct and unreached code is not necessarily dead; no percentage authorizes exposure or deletion |
| Dynamic/platform retention | Dynamic plugins, non-CLI entrypoints, and Pi/vehicle platform paths remain intentionally retained under their recorded owners |

### Durable decision after M007

The closeout implementation records **resume the existing M006 milestone**
after Phase C. At proposal time, canonical M006 is Active with
`Cross-environment shadow proposal evidence` in `ready_for_proposal`; its
action policy continues to forbid applied control. Phase A refreshes and cites
that state. If M006 has materially changed before implementation, stop for
proposal review rather than inventing a different cross-milestone handoff.

No M006 branch, plan, evidence, or product file changes under `m007/closeout`.

### Required outputs by phase

#### Phase A — implementation PR

1. Create `docs/milestones/007-cli-operator-usability/closeout.md` with the
   required sections below.
2. Append the M007 entry to `docs/milestones/completed.md` without changing
   prior entries.
3. Update `docs/README.md` navigation only as needed for a closing/closed M007;
   do not copy milestone status or architecture into it.
4. Reconcile root `README.md` and
   `docs/reference/cli-simulator-perception-journey.md` against the accepted
   command/help surface. Modify only factual documentation drift; do not change
   product code to make documentation pass.
5. Update cumulative PR #81's body using the milestone template: current
   objective, completion-usage link, accepted review units #84, #88, #100,
   #107, #122, and #138, unresolved residuals, exact final validation, and
   correct milestone/main topology. Leave it draft until Phase B completes.
6. Optionally update non-terminal `plan.md` Closeout/Milestone Decisions prose
   to point at the durable judgment. Do not change criteria, risks, accepted
   ledger, current identity, or Status.
7. Do not change runtime, CLI, tests of new behavior, evidence records, or
   M006 artifacts.

#### Phase B — mechanical handoff

After the accepted implementation PR merges, run
`complete-implementation`. It alone:

- marks M007-06 `Met`;
- removes the eight active risk rows after their residual meaning is preserved;
- adds the accepted closeout implementation ledger/history rows;
- sets Status `closed` and Current frontier `None (closed)`;
- keeps the frontier map empty; and
- regenerates `plan.html`.

The handoff commit changes only `plan.md` and `plan.html`.

#### Phase C — operator integration

1. Mark cumulative PR #81 ready.
2. Review M007 objective, completion usage, all accepted units, closeout
   judgment, final validation, and residual limits as a whole.
3. If that review requests changes against the existing M007 completion
   contract, do not merge or tag. Record the verdict and repair revision in
   #81's Repair Cycle Ledger, apply the bounded repair on the milestone branch,
   refresh affected closeout text, the candidate M007 `completed.md` entry, and
   validation, and re-review the exact new #81 head. Prior completed-milestone
   entries remain unchanged. There is no cycle-count stop.
4. Classify an independent improvement that does not falsify M007 as P3 or a
   later residual. Do not reopen the closed plan or create a work node for it.
   If a finding truly requires changing milestone scope, exit criteria, or
   recovery mechanics, keep #81 unmerged and first land a separate canonical
   process change; this proposal grants no ad hoc reopen authority.
5. Only after an exact-head accepted whole-milestone review, merge #81 into
   `main` with a merge commit and tag that mainline merge
   `milestone-007`.
6. Remove obsolete M007 milestone/proposal/implementation branches only after
   the merge is durable.
7. Resume M006 through its own canonical branch and workflow; do not carry M006
   changes in the M007 cumulative merge beyond already-shared ancestry.

### Required `closeout.md` sections

- **Outcome** — close date, whole-milestone result, action policy, and the
  distinction between Phase A judgment and Phase B terminal mutation.
- **Durable Decisions** — passive attachment, layer-state vocabulary,
  observation-only authority, explicit external recovery, machine-first/HITL
  sequencing, informational coverage, complete leaf accounting, and owned
  capability dispositions.
- **Completion Usage** — the supported primary command sequence and supporting
  inspection/recovery/evidence workflows, with links to the durable guide.
- **What Was Demonstrated** — criterion/review-unit/evidence table covering all
  accepted M007 units.
- **Sequence And Capability Accounting** — every US disposition, every LIVE
  residual owner, all ten capability groups, and the zero-remove fact.
- **Failures And Residual Limits** — the table above, including open issues and
  unsupported Pi/remote/movement claims.
- **Validation** — exact deterministic and offline evidence-integrity commands
  and results at the closeout implementation tip; accepted live evidence cited,
  not relabeled as newly run.
- **Deferred Work** — #89–#91, `M007-LIVE-*`, deferred/blocked US rows, the
  `cli-operator-surfaces` expose candidate, and the decision to resume M006.
- **Cumulative PR Identity** — #81, base `main`, head milestone branch, and
  “ready after Phase B” responsibility.
- **References** — plan, completed ledger, durable operator guide, accepted
  implementation PRs, and tracked evidence directories.

### Evidence rendering

- **Derived HTML:** Skip.
- **Reason:** Closeout creates no new sealed machine-readable signal. It cites
  existing sealed records and their committed derived views; `closeout.md` is
  the human judgment authority.

## Ownership

| Concern | Owner |
| --- | --- |
| Whole-milestone judgment and residual restatement | Phase A `closeout.md` |
| Durable CLI documentation reconciliation | Phase A root README + reference guide audit |
| Evidence identities and accepted-result mapping | Accepted M007 ledger/artifacts; Phase A cites them |
| M007-06 Met, risk clear, terminal status, empty frontier | Phase B `complete-implementation` using Expected Handoff |
| Completed ledger and documentation navigation | Phase A |
| Cumulative PR #81 body and final validation | Phase A; readiness/merge remain Phase C |
| M006 next-focus state | Canonical M006 plan; Phase A cites, Phase C resumes separately |
| Follow-on product issues/capability exposure | Later proposals; outside closeout |

## Affected Paths

- Successful Phase A creates the closeout judgment and updates only the
  documentation/ledger paths declared below; product and evidence authorities
  remain byte-stable.
- Successful Phase B changes only the M007 plan and generated plan HTML through
  the reviewed handoff.
- Successful Phase C changes GitHub/cross-milestone state only after whole-
  milestone review.
- Any non-closeout criterion no longer `Met`, missing authority, or unsupported
  completion claim blocks closeout.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| Any of M007-01–05 or M007-07–10 is no longer `Met` at implementation start | Stop; closeout cannot repair or hide the gap |
| Phase A sets M007-06 `Met`, clears risks, edits accepted ledger rows, empties current, or sets Status `closed` | Reject; terminal facts belong to Phase B |
| Phase A changes CLI/runtime code or tests new behavior | Reject as a different review unit |
| Closeout claims the accepted view was exact-current | Reject; accepted correlation was bounded-stale lag 15 within 24 |
| Closeout relabels historical coverage or cited sequence evidence as current HEAD proof | Reject |
| Any US-01..US-10 row disappears, or deferred/blocked becomes passed without new accepted evidence | Reject |
| `M007-LIVE-*`, #89, #90, or #91 disappears because it is non-gating | Reject; retain explicit residual disposition |
| Plan risk says findings lack owners without reconciling M007-08's committed owners | Reject stale closeout statement |
| Any capability group is omitted, `expose` is called implemented, or `retain` is called journey-covered | Reject |
| Unreached member counts or coverage percentages authorize product exposure/deletion | Reject |
| Closeout runs hazardous, movement, destructive, external, or Pi leaves merely to refresh proof | Reject |
| Closeout claims PiRacer parity, remote hosting, autonomous movement, or non-idle safety | Reject |
| Existing evidence JSON or HTML is regenerated just to obtain a newer timestamp | Reject; preserve accepted bytes |
| M007-07 `verify-report` is run from the squash-merged milestone/closeout head | Reject; resolve the exact frozen PR #107 head, verify under that original ancestry, and require byte equality with the closeout report |
| `closeout.md` omits an active-plan risk before Phase B removes it | Reject |
| Cumulative PR #81 retains its stale “None yet” accepted-unit body | Reject Phase A completeness |
| Implementation PR claims cumulative PR #81 is ready or merged | Reject; that is Phase C |
| M007 closeout edits or implements M006 | Reject cross-milestone scope leak |
| Phase C starts before the terminal handoff commit | Reject; cumulative readiness follows Phase B |
| Phase 0 finds a product/evidence gap required by an existing criterion | Do not accept or merge #143; close it before selecting a new owned proposal from the unchanged Active/idle milestone branch |
| Phase C finds an existing-contract defect on #81 | Keep #81 unmerged; record and repair the finding in #81's Repair Cycle Ledger; refresh affected closeout truth and validation; re-review the exact new head |
| Phase C treats a new independent want as a completion blocker | Classify it P3 or later residual work; do not reopen M007 for unrelated scope |
| Phase C uses an exceptional handoff or edits a closed plan to create a new node | Reject; #143 grants no reopen authority. A real scope/process gap requires a separate canonical process change before #81 can proceed |
| M007 is absent from `completed.md` in #81's cumulative diff | Reject; the completed-ledger append belongs to Phase A and precedes readiness, review, merge, and tag |

## External Assumptions

- Accepted implementation PRs #84, #88, #100, #107, #122, and #138 and their
  plan ledger rows remain the acceptance authority.
- Tracked evidence and reference documentation named above remain committed and
  offline-verifiable.
- GitHub cumulative PR #81 remains open from
  `milestone/007-cli-operator-usability` to `main`; its body is stale and must
  be reconciled in Phase A.
- Issues #89–#91 remain open follow-on work at proposal time. Phase A refreshes
  state and records any accepted disposition change without implementing it.
- Issues #139 and #141 remain closed with their current superseded/completed
  reasons.
- Canonical M006 remains independently owned by
  `milestone/006-decision-facing-perception-readiness`; at proposal time it is
  Active and its cross-environment evidence frontier is `ready_for_proposal`.

## Non-Goals

- Product, runtime, CLI, test-feature, or Metrics UI changes.
- Fixing #89 or implementing #90/#91.
- Implementing the `cli-operator-surfaces` expose candidate or refactoring any
  retained capability group.
- Recapturing live acceptance, scenario continuity, journey coverage, surface
  audit, or capability disposition evidence solely for recency.
- Promoting deferred/blocked US rows, running unsafe leaves, or setting a
  coverage threshold.
- Claiming PiRacer live parity, remote/public views, applied movement, non-idle
  authority, semantic correctness, or dead code.
- Editing, activating, or implementing M006 under the M007 branch.
- Marking/merging cumulative PR #81 or deleting branches before Phase B.
- Reopening a closed M007 plan through an unreviewed receipt, or creating a new
  work node after Phase B.
- Treating Phase C as a source of independently scoped feature wants; those are
  P3 or later residuals unless they falsify the existing milestone contract.

## File Impact

### Proposal PR only

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/closeout.md` | This reviewed contract |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | Select closeout as current in `proposal_in_review`; leave criteria, risks, ledger, status, and empty remaining path unchanged |

### Expected Phase A implementation PR

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/closeout.md` | Create durable whole-milestone judgment |
| `docs/milestones/completed.md` | Append M007 entry only |
| `docs/README.md` | Navigation only, if needed |
| `README.md` | Reconcile durable journey link/summary only if audit finds drift |
| `docs/reference/cli-simulator-perception-journey.md` | Reconcile accepted command/help facts only if audit finds drift |
| M007 `plan.md` / `plan.html` | Optional non-terminal prose only; normal start-implementation state transition remains workflow-owned |
| Cumulative PR #81 body | External GitHub update with accepted units, residuals, and exact final validation; keep draft |

No Phase A changes are permitted below `autonomy/`, `implementations/`, `cli/`,
`tests/`, any M007 `evidence/` or `tools/` directory, or the M006 milestone.

### Phase B mechanical changes

- M007 `plan.md` and generated `plan.html` only, through
  `complete-implementation`.

### Phase C external changes

- Cumulative PR #81 ready/review; any existing-contract findings and repair
  cycles remain on #81. Exact-head acceptance permits merge, tag, branch
  cleanup, then separate M006 workflow resumption.

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
  --head-ref m007/closeout-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

Review confirms proposal-only paths, one whole-milestone question, Review Kind
`Milestone closeout`, the Phase 0 node-routing gate, the Phase A/B/C boundary,
canonical completed-ledger order, exact residual accounting, and no terminal
plan mutation.

### Phase A implementation PR

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py

set -euo pipefail
M007_ACCEPTED_COVERAGE_HEAD='fda10c6b6f7fe98c7904d0b9bbfa1bc45c6b671b'
M007_COVERAGE_REPORT='docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json'
M007_VERIFY_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/m007-coverage-verify.XXXXXX")"
M007_VERIFY_WORKTREE="$M007_VERIFY_ROOT/accepted-head"
cleanup_m007_verify() {
  git worktree remove --force "$M007_VERIFY_WORKTREE" >/dev/null 2>&1 || true
  rmdir "$M007_VERIFY_ROOT" >/dev/null 2>&1 || true
}
trap cleanup_m007_verify EXIT

git fetch --no-tags origin pull/107/head
M007_FETCHED_HEAD="$(git rev-parse FETCH_HEAD)"
test "$M007_FETCHED_HEAD" = "$M007_ACCEPTED_COVERAGE_HEAD"
git worktree add --detach "$M007_VERIFY_WORKTREE" "$M007_ACCEPTED_COVERAGE_HEAD"
cmp "$M007_COVERAGE_REPORT" "$M007_VERIFY_WORKTREE/$M007_COVERAGE_REPORT"
(
  cd "$M007_VERIFY_WORKTREE"
  export PYTHONDONTWRITEBYTECODE=1
  export M007_COVERAGE_PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
  docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session \
    verify-report "$M007_COVERAGE_REPORT"
)
cleanup_m007_verify
trap - EXIT

python3 docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/validate_audit.py
python3 docs/milestones/007-cli-operator-usability/tools/capability-disposition/capability_disposition.py validate
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/closeout \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

The implementation records exact results at its final head. The M007-07 check
must resolve `pull/107/head` to the frozen full commit, pass the accepted
verifier under that commit's original ancestry, and prove byte equality between
that verified report and the report at the closeout head. It additionally
checks that accepted evidence paths exist, frozen digests and summary facts
match this contract, the durable guide agrees with parser/help output, Phase A
left all other accepted evidence bytes unchanged, and cumulative PR #81's
updated body matches the closeout judgment.

No live simulator, browser, PiRacer, movement, or evidence recapture is required
unless an accepted authority is missing or contradicted; that condition blocks
closeout rather than silently expanding Phase A.

### Phase B and Phase C

```sh
python3 docs/milestones/workflow.py complete-implementation \
  --plan docs/milestones/007-cli-operator-usability/plan.md \
  --pr <implementation-pr-number>

python3 docs/milestones/workflow.py status \
  --plan docs/milestones/007-cli-operator-usability/plan.md
```

Phase B must report M007 closed with every criterion `Met`, no current or
remaining frontier, and an accepted closeout ledger row. Phase C then performs
the whole-milestone review. A changes-requested verdict stays on #81 and is
repaired and re-reviewed there; only an exact-head accepted #81 may merge, be
tagged, and permit branch cleanup.

## Expected Handoff

Post-merge successful closeout implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "close",
  "result": "Accepted",
  "durable_evidence": "M007 closeout judgment in closeout.md; completed.md 007 entry; durable CLI documentation reconciled; accepted journey, live, coverage, audit, and capability evidence mapped; sequence/capability residuals and M006 resume decision recorded; cumulative PR #81 prepared for post-handoff whole-milestone review in implementation PR #{pr}",
  "criterion_updates": {
    "M007-06": {
      "status": "Met",
      "evidence": "Closeout confirms the supported primary journey and durable CLI documentation, maps accepted coverage and full-leaf audit evidence, verifies accountable US-01 through US-10 and capability dispositions, and records external simulator, PiRacer, remote-view, non-idle-control, deferred-product, and historical-measurement limits in PR #{pr}"
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

1. Complete the Phase 0 whole-milestone closeout-readiness review on #143. If
   it finds a required product/evidence gap, do not accept #143; close it before
   selecting a new proposal from the still-Active/idle milestone branch.
2. Accept this proposal with an exact-head contract review, merge it to the
   milestone branch, and run `workflow.py accept-proposal` for its PR number.
3. Start `m007/closeout` with `workflow.py start-implementation`.
4. Implement Phase A only; open the closeout implementation PR and update draft
   cumulative PR #81 without marking it ready.
5. After exact-head implementation acceptance, squash-merge the closeout PR.
6. Run `complete-implementation` to apply Phase B and commit only the generated
   terminal plan transition.
7. Perform Phase C: mark #81 ready and review it as a whole. Repair any
   existing-contract finding in #81 with its repair ledger and exact-head
   re-review. Only after acceptance, merge with a merge commit, tag
   `milestone-007`, clean M007 branches, then resume M006 separately.

## Review Kind

**Milestone closeout** — whole-milestone acceptance judgment over completion
usage, accepted review units, durable evidence and documentation, residual
limits, cumulative integration, and the next cross-milestone focus.
