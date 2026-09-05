# Milestone 007 Closeout: CLI Operator Usability

Status: Phase A closeout packet prepared 2026-08-24; requalified 2026-08-26

Milestone 007 remains `Active`, M007-06 remains `Partial`, and cumulative PR
[#81](https://github.com/GeorgeLuo/auto-driving/pull/81) remains draft and
unmerged. This document is the retained Phase A judgment, updated in place
after the rejected cumulative review and accepted repairs #146, #154, and
#155. The mechanical Phase B handoff and the independent Phase C
whole-milestone review remain required.

## Outcome

Original closeout judgment date: **2026-08-24**. Requalification date:
**2026-08-26**. The whole-milestone result is ready for the reviewed terminal
handoff and a fresh cumulative review at the accepted evidence boundaries; it
is not yet terminally closed or whole-milestone accepted.

The accepted M007 review units support closing the CLI Operator Usability
milestone at their recorded evidence boundaries. They delivered a discoverable
passive Chase journey, exact layer and recovery semantics, observation-only live
acceptance, representative machine-first/HITL scenario continuity, reproducible
named-context coverage, complete CLI-leaf and US-01 through US-10 accounting,
and an owned disposition for production capabilities outside those journeys.
The Phase 0 review of proposal #143 found no product or evidence gap requiring a
new in-milestone work node.

Phase C later rejected cumulative PR #81. That rejection is retained history,
not a reason to rewrite the original packet or to treat implementation
[#144](https://github.com/GeorgeLuo/auto-driving/pull/144) as accepted
cumulative closure. Accepted repairs #146, #154, and #155 closed the three
blocking product findings. This requalified Phase A rechecks those owners,
reconciles the retained packet and append-only ledger, and refreshes draft
PR #81 without marking it ready.

This is not yet whole-milestone acceptance:

- **Phase A** publishes this requalified judgment, the append-only
  requalification ledger section, documentation reconciliation, and the
  updated draft #81 review surface.
- **Phase B**, only after this implementation PR is accepted and squash-merged,
  applies the reviewed handoff that marks M007-06 `Met`, removes the eight plan
  risk rows, sets Status `closed`, and leaves no in-milestone frontier.
- **Phase C** then reviews cumulative PR #81 as a whole. Only an exact-head
  acceptance may merge #81 into `main` and tag that merge `milestone-007`.

The action policy remains observation-only. No accepted M007 live path applied
vehicle movement, and this closeout makes no autonomous-movement, non-idle
control, PiRacer parity, or remote-hosting claim.

## Rejected Cumulative Review And Restore

These identities are the durable rejection/restore anchors. They are preserved
verbatim and are not a later `main` merge or `milestone-007` tag.

| Fact | Identity |
| --- | --- |
| Rejected cumulative PR #81 head | `ee2e3056f77bee9a4511877829eb9c46b52d0aa2` |
| Restore head | `9f758d9927d8b870b1d3d2219441fd7410d64b47` |
| Rejected-review receipt | [`#81 changes_requested`](https://github.com/GeorgeLuo/auto-driving/pull/81#pullrequestreview-5015145766) |
| Accepted Phase B handoff receipt for #144 | [`#144 accepted`](https://github.com/GeorgeLuo/auto-driving/pull/144#pullrequestreview-5015032543) |
| Restore commit | `9f758d9927d8b870b1d3d2219441fd7410d64b47` |

[#144](https://github.com/GeorgeLuo/auto-driving/pull/144) remains in milestone
ancestry as the original Phase A packet implementation. Its plan ledger result
is `Cumulative review rejected; closeout packet retained; M007-06 remains
Unmet`. That is not whole-milestone acceptance.

## Accepted Repair Ancestry

The three Phase C product findings were rechecked at their accepted owners:

| Finding | Accepted repair | Required result preserved here |
| --- | --- | --- |
| Primary commands leaked malformed timeout `ValueError` | [#146](https://github.com/GeorgeLuo/auto-driving/pull/146); reviewed head `787f9f967c6b0ed276036943a5122e11c4a424be`; milestone merge `f6d221c0c602e648efc4bdd355c909a9bca3fa12` | `vehicles status`, `vehicles automation run`, and `vehicles update perception` reject `0`, negative, `nan`, `-nan`, `inf`, `+inf`, and `-inf` before dispatch with exit 2, stable human/JSON input errors, no traceback or side effect; finite positive and default timeout behavior remains unchanged |
| Staged PiRacer inspection hid reachable live state/view | [#154](https://github.com/GeorgeLuo/auto-driving/pull/154); reviewed head `d6120956a5a14ccbbb754b89379e79e6f8ccf4d4`; milestone merge `1b08ff596df9b2a9ad23ef1d2947ccf85cb0f551` | `vehicles info perception --id piracer` preserves valid offline staged `active.json`, enriches it with reachable live observation and local-view state, reports staged/live availability consistently in human and JSON output, and treats live outage as unavailable live state without staging, worker, control, or input actions |
| Chase accepted decoded dimension and MIME/format mismatches | [#155](https://github.com/GeorgeLuo/auto-driving/pull/155); reviewed head `23982845948a61346953d285aba2eaeb5de34418`; milestone merge `ff6c00f2ac98a40f2aab9cfa198fc9bb3d0da386` | Decoded dimensions, raster format, data-URL MIME, and declared content type agree before write/publish; invalid cases fail `capture_image_invalid` before publication; supported PNG/JPEG/GIF/WEBP captures and optional evaluator-reference independence remain intact |

M007-03 therefore cites #84 plus #155. M007-04 cites #84 plus #146. The PiRacer
inspection portion of M007-06 cites #154. M007-06 remains `Partial` until the
Phase B handoff.

## Durable Decisions

- The primary Chase path **passively attaches** to the vehicle already exposed
  by Metrics UI. Status, staging, observation-only startup, view inspection,
  and cleanup do not silently select a scenario, change playback, take control,
  or inject an input.
- Operator state remains separated into simulator server, frontend, game,
  vehicle, passive capture, deployment, worker, perception view, and optional
  evaluator-reference layers. “Discoverable” or “active” does not imply that a
  bundle is deployed, a worker is running, or a current-generation view is
  healthy.
- Camera/frame identity is independent from evaluator-only control reference.
  Sensor-only observation remains available without evaluator reference;
  reference-dependent scoring stays fail-closed. Declared image dimensions,
  raster format, data-URL MIME, and content type must agree before write or
  publish.
- Observation-only authority is explicit and testable. A processed frame and a
  healthy view do not confer movement authority, and recording remains opt-in.
- External simulator recovery is explicit. Missing frontend, game, capture, or
  preservation capability produces a boundary-specific failure and minimum
  requested action instead of hidden simulator reconfiguration.
- Realistic scenario evidence remains **machine-first**, with HITL only after
  machine gates pass and only where visual judgment owns acceptance. Cleanup
  and restoration are part of the result.
- Accepted evidence is retained at its recorded identity. Closeout cites live
  and historical artifacts; it does not recapture them for a newer timestamp or
  relabel them as proof of the current environment.
- Journey coverage is informational. Executed code is not necessarily correct,
  unreached code is not necessarily dead, and no percentage authorizes feature
  exposure or deletion.
- The parser is the public-leaf membership authority. All 49 leaves and all ten
  #88 usage sequences have a committed disposition, including honest deferred
  and blocked rows.
- Every capability outside the declared journey set has an owner and an
  `expose`, `retain`, or `remove` candidate. `expose` is not implemented work,
  `retain` is not a journey-coverage claim, and zero groups are authorized for
  removal.

## Completion Usage

The supported primary workflow is maintained in the
[Chase simulator-to-perception CLI journey](../../reference/cli-simulator-perception-journey.md).
Run it from the repository root:

```sh
./cli/automa vehicles status --chase-url http://localhost:5050

./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --algorithm lightweight_observer

./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
  --open-view

./cli/automa vehicles status --id chase-sim-chaser

./cli/automa vehicles automation stop --id chase-sim-chaser

./cli/automa vehicles status --id chase-sim-chaser
```

The first post-start status must show a deployed bundle, a running
observation-only worker, a current-generation view, and no applied control. The
opened page must show a camera frame and frame-matched or bounded-stale
perception result. Final status must show the worker stopped and no
current-generation view while leaving the deployment staged.

Supporting workflows remain available within their documented boundaries:

| Workflow | Supported use | Boundary |
| --- | --- | --- |
| Discover and inspect | Descend with group `help`, then use human or `--json` status/info output | Public parser/help and the layer-state vocabulary are authoritative |
| Recover startup | Follow the single emitted next action for the failed layer | Simulator-changing `simulators ensure` is explicit and never a hidden passive-path recovery |
| Exercise realistic scenarios | Run the repository-owned continuity catalog machine-first, then perform named HITL checks | Three required families are accepted; optional and exact-step US rows retain their dispositions below |
| Verify journey coverage | Use the sealed M007-07 manifest/report verifier under the frozen accepted PR #107 head | Historical reachability only; no correctness, dead-code, or percentage-gate claim |
| Audit public CLI use | Validate the parser-derived leaf inventory and US registry | Hazardous, movement, physical, destructive, and external leaves need not be run merely for accounting |
| Review unreached capability | Validate the M007-09 disposition record and dashboard | Candidates require later review before exposure, refactor, or removal |

## What Was Demonstrated

| Criteria | Accepted unit | Demonstrated result | Durable authority and limit |
| --- | --- | --- | --- |
| M007-01–M007-02 | [#84](https://github.com/GeorgeLuo/auto-driving/pull/84) | One supported passive Chase workflow; distinct layer states; exact recovery; preserved scenario/playback/control/input | [Operator guide](../../reference/cli-simulator-perception-journey.md); local Chase only, no implicit simulator preparation |
| M007-03 | [#84](https://github.com/GeorgeLuo/auto-driving/pull/84) plus [#155](https://github.com/GeorgeLuo/auto-driving/pull/155) | Observation-only publication when sensor image and frame identity are valid without evaluator reference; invalid decoded dimensions, raster format, data-URL MIME, or declared content type fail `capture_image_invalid` before write/publish | [Operator guide](../../reference/cli-simulator-perception-journey.md); Chase adapter envelope closed in #155; optional evaluator-reference independence preserved |
| M007-04 | [#84](https://github.com/GeorgeLuo/auto-driving/pull/84) plus [#146](https://github.com/GeorgeLuo/auto-driving/pull/146) | Bounded operation timeout with stable human/JSON errors; `vehicles status`, `vehicles automation run`, and `vehicles update perception` reject non-positive and non-finite `--timeout-s` before dispatch | [Operator guide](../../reference/cli-simulator-perception-journey.md); timeout input-envelope closed in #146 |
| M007-05 | [#88](https://github.com/GeorgeLuo/auto-driving/pull/88) | `pass` at auto-driving `caf335797b71df1323736a2054934b7c211418b0` and Metrics UI `722e070fdc9f4ee89d13f947bf3996e62dcb2783`; 148 processed frames; healthy loopback view; no movement, no default recording, protected state preserved, worker cleaned up | `evidence/live-cli-acceptance/result.json`; accepted correlation was bounded-stale lag 15 within bound 24, not exact-current and not a current-environment guarantee |
| M007-10 | [#100](https://github.com/GeorgeLuo/auto-driving/pull/100) | `result: pass` at behavior head `37b7393fe759f1597860a30d8c10ca5692f1c0cc`; required `continuity.offline_perception`, `continuity.live_config_swap`, and `continuity.memory_lifecycle` families passed; machine-first/HITL, activation restoration, cleanup, and freshness finalizer passed | `evidence/cli-scenario-continuity/result.json`; optional families and exact-step sequence claims remain residual |
| M007-07 | [#107](https://github.com/GeorgeLuo/auto-driving/pull/107) | Reproducible branch-aware owned-Python attribution for 34 commands/contexts, 37 raw shards, and 63 represented owned files at source commit `7931fa9a995af5626fabef818f9e28b98c73e299` | `evidence/cli-journey-coverage/report.json`, digest `51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789`; behavioral correctness not evaluated |
| M007-08 | [#122](https://github.com/GeorgeLuo/auto-driving/pull/122) | All 49 parser leaves accounted for: 32 action, 10 meta, 7 alias; zero unclassified leaves; all US-01 through US-10 rows defined and dispositioned; help drift `ok` | `evidence/cli-surface-audit/report.json`; deferred and blocked rows remain below with owners and unlocks |
| M007-09 | [#138](https://github.com/GeorgeLuo/auto-driving/pull/138) | 96 sealed source members, 93 candidates, all 93 assigned across ten owned groups; nine `retain`, one `expose`, zero `remove`; zero residual membership errors | `evidence/capability-disposition/record.json`, digest `81ce4993fe8624bbc818bcad7142dafb78e2be1ef6c45a6115ae535a51477e6f`; historical disposition, not implementation authorization |
| M007-06 | This requalified Phase A judgment plus the later Phase B handoff; PiRacer inspection portion cites [#154](https://github.com/GeorgeLuo/auto-driving/pull/154) | Documentation, evidence identities, complete sequence/capability accounting, residual limits, rejected-review history, #146/#154/#155 ancestry, #81 identity, and the next-focus decision are durable; staged PiRacer inspection remains available offline while reachable live state/view is reported without making live outage fail the offline path | Remains `Partial` in Phase A; only the accepted closeout implementation handoff may mark it `Met` |

## Sequence And Capability Accounting

### US-01 through US-10

The registry outcome is preserved exactly; accepted family-level continuity is
not promoted into an exact-step sequence pass.

| ID | Operator question | Disposition | Owner / unlock |
| --- | --- | --- | --- |
| US-01 | Discover the supported passive workflow without parser/runtime knowledge | `passed` | Accepted help-discovery evidence |
| US-02 | Attach to the current Chase session, inspect perception, and leave no worker without changing simulator state | `passed` | Accepted passive-journey evidence |
| US-03 | Compare packaged or ready candidates on the same captured frames | `deferred` | `cli-perception-offline`; exact-step #88 US-03 `visual_observer` apply + compare evidence after citation amendment; family aggregate is insufficient |
| US-04 | Swap perception configuration while preserving the simulator environment | `deferred` | `cli-perception-plugins`; exact-step #88 US-04 swap, disable, and second-run evidence after citation amendment |
| US-05 | Show attributable retained evidence through survival, expiry, and reset | `deferred` | `cli-memory-lifecycle`; exact-step #88 US-05 `visual_observer` + stream + memory-check evidence after citation amendment |
| US-06 | Compare motion-tracking value against churn/capacity pressure | `deferred` | `cli-perception-plugins`; seal `continuity.plugin_ablation` or a successor with HITL |
| US-07 | Judge expensive-observer backpressure against overlay and memory usefulness | `deferred` | `cli-automation-status`; seal the temporal-backpressure sequence |
| US-08 | Reset suspicious retained evidence and observe a new epoch repopulate | `deferred` | `cli-memory-lifecycle`; exact-step #88 US-08 reset/repopulation evidence after citation amendment |
| US-09 | Reproduce a live memory anomaly deterministically offline | `deferred` | `cli-memory-replay`; seal the deterministic-replay sequence |
| US-10 | Qualify a candidate on labeled physical-check behavior | `blocked` | `physical-perception-lab`; labeled physical corpus required |

### Live residual registry

| ID | Disposition | Owner | Residual |
| --- | --- | --- | --- |
| `M007-LIVE-001` | `deferred` | `cli-perception-apply` | P2: recorded apply run-id collisions can overwrite sibling artifacts; issue #89 owns the product bug |
| `M007-LIVE-002` | `deferred` | `lab-candidates-compare` | P2: candidate readiness inventory can disagree with compare-path resolution |
| `M007-LIVE-003` | `deferred` | `cli-perception-compare` | P3: failed compare can dump full multi-frame JSON on the human surface |
| `M007-LIVE-004` | `deferred` | `cli-perception-review-ux` | P3 enhancement: consolidated multi-engine review / open-review surface |
| `M007-LIVE-005` | `deferred` | `cli-perception-run` | P3: `perception run --json` buries the review path as an operator scan surface |

These owners supersede the active plan risk’s historical “without owners”
wording; the underlying product gaps remain residual.

### Capability dispositions

| Group | Disposition | Owner | Reason |
| --- | --- | --- | --- |
| `autonomy-decision-runtime` | `retain` | `repo_path:autonomy` | Non-CLI runtime boundary |
| `autonomy-perception-plugins` | `retain` | `repo_path:autonomy/perception` | Dynamic plugin boundary |
| `autonomy-vehicle-boundary` | `retain` | `repo_path:autonomy/vehicle` | Vehicle/platform boundary |
| `cli-operator-surfaces` | `expose` | `repo_path:cli/automa_cli` | Named CLI gap for a later exposure review; not implemented by M007 closeout |
| `implementation-memory` | `retain` | `repo_path:implementations/memory` | Non-CLI runtime boundary |
| `implementation-operations` | `retain` | `repo_path:implementations/operations` | Vehicle/platform operation boundary |
| `implementation-package-boundaries` | `retain` | `repo_path:implementations` | Implementation namespace ownership |
| `implementation-perception` | `retain` | `repo_path:implementations/perception` | Dynamic plugin boundary |
| `implementation-runtime` | `retain` | `repo_path:implementations/runtime` | Donkey runtime/platform boundary |
| `implementation-vehicle` | `retain` | `repo_path:implementations/vehicle` | Vehicle/platform boundary |

All ten groups are accounted for. Nine remain retained, one is a later expose
candidate, and zero are removal candidates.

## Failures And Residual Limits

| Residual | Durable limit |
| --- | --- |
| External Metrics UI drift | Live acceptance proves only Metrics UI commit `722e070fdc9f4ee89d13f947bf3996e62dcb2783`; future capture-contract drift requires a new bounded live unit |
| Frontend registration timing | Browser presence and Play WebSocket readiness can differ; bounded readiness and exact recovery mitigate but do not eliminate external timing variance |
| Evaluator reference boundary | Sensor-only observation remains valid without evaluator reference; reference-dependent evidence remains fail-closed |
| Browser launch and remote view | View health is authoritative; OS browser launch is non-fatal and platform-dependent; public or non-loopback remote hosting is unsupported |
| PiRacer and hazardous leaves | M007 did not run hardware, movement, destructive, or external-state leaves merely for coverage; US-10 remains blocked on labeled physical data. #154 closed the staged-inspection regression without claiming live PiRacer parity |
| Applied movement and non-idle control | Every accepted M007 live path is observation-only; no autonomous-movement safety or non-idle authority is claimed |
| Deferred product usability | Open issue [#89](https://github.com/GeorgeLuo/auto-driving/issues/89) and `M007-LIVE-001..005` remain owned defects/candidates; [#90](https://github.com/GeorgeLuo/auto-driving/issues/90) and [#91](https://github.com/GeorgeLuo/auto-driving/issues/91) remain larger same-frame experiment and transactional live-trial features |
| Historical sequence evidence | Cited `passed` sequences are historical, not continuous verification of later heads |
| Historical reachability | M007-07 and M007-09 are sealed historical measurements; later product changes require recapture before new reachability claims |
| Coverage interpretation | Executed code is not necessarily correct and unreached code is not necessarily dead; no percentage authorizes exposure or deletion |
| Dynamic/platform retention | Dynamic plugins, non-CLI entrypoints, and Pi/vehicle platform paths remain intentionally retained under their recorded owners |

Issue [#139](https://github.com/GeorgeLuo/auto-driving/issues/139) is closed as
not planned because PR #138’s accepted dashboard superseded it. Issue
[#141](https://github.com/GeorgeLuo/auto-driving/issues/141) is closed as
completed by the integrated command-display adjunct. Neither requires more M007
product work.

## Validation

### Original Phase A (2026-08-24)

The original packet was validated in a coherent Python 3.11.7 environment with
the interpreter directory prepended to `PATH`:

```sh
PATH=/opt/homebrew/anaconda3/bin:$PATH \
  PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
```

Result: `Ran 820 tests`; `OK (skipped=2)`, with zero failures or errors and two
expected live-environment skips.

The frozen M007-07 verifier resolved `pull/107/head` to
`fda10c6b6f7fe98c7904d0b9bbfa1bc45c6b671b`, proved byte equality between the
accepted-head and closeout-head reports, and returned `result: pass` with
report digest
`51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789`.

Accepted live artifacts were cited, not rerun. That original run did not launch
a simulator/browser, contact PiRacer, command movement, or regenerate accepted
evidence for recency.

### Requalification Phase A (2026-08-26)

Requalification used the same coherent Python 3.11.7 interpreter path as the
original packet. Focused public-door tests for the three accepted repairs:

```sh
PATH=/opt/homebrew/anaconda3/bin:$PATH \
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.cli.vehicles.test_timeout_input \
  tests.cli.perception.test_commands \
  tests.implementations.vehicle.test_chase_frame_identity
```

Result: `Ran 54 tests`; `OK`. Those modules cover the #146 `--timeout-s` cases
(`0`, `-1`, `nan`, `-nan`, `inf`, `+inf`, `-inf`) and no-dispatch assertions
for `vehicles status`, `vehicles automation run`, and `vehicles update
perception`; the #154 staged/local-plus-reachable and unavailable-live
human/JSON PiRacer inspection cases; and the #155 decoded
dimension/raster/MIME/content-type mismatch, supported-raster, optional
evaluator-reference, and pre-publication cases.

Inherited suite and evidence commands:

```sh
PATH=/opt/homebrew/anaconda3/bin:$PATH \
  PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
```

Result: `Ran 844 tests`; `OK (skipped=2)`, with zero failures or errors and two
expected live-environment skips.

The frozen M007-07 verifier again resolved `pull/107/head` to
`fda10c6b6f7fe98c7904d0b9bbfa1bc45c6b671b`, proved byte equality between the
accepted-head and requalification-head reports, and returned `result: pass`
with report digest
`51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789`.

The remaining accepted validators returned:

- CLI surface audit: `result: pass`, 49 leaves.
- Capability disposition: `result: pass`, 93 candidate members across ten
  groups, record digest
  `81ce4993fe8624bbc818bcad7142dafb78e2be1ef6c45a6115ae535a51477e6f`.
- Milestone workflow validation, generated-Markdown check, and
  `git diff --check`: pass.
- Parser/help audit for root help, vehicle help, automation help, run/status,
  and perception-update surfaces: pass. The root README, docs navigation, and
  durable operator guide already agree with those surfaces, so no prose change
  was needed.
- Product, test, accepted evidence, tooling, and prior-proposal paths: present
  and byte-stable against the milestone base; frozen JSON identities and
  proposal facts match.

Accepted live artifacts were cited, not rerun. Requalification did not launch a
simulator/browser, contact PiRacer, command movement, or regenerate accepted
evidence for recency. The three original Phase C findings do not reproduce at
this head.

## Deferred Work

- Fix open [#89](https://github.com/GeorgeLuo/auto-driving/issues/89) only in a
  separately reviewed product unit; retain #90 and #91 as larger feature
  candidates.
- Preserve the five owned `M007-LIVE-*` residuals and the deferred/blocked US
  rows above. None is silently promoted by closeout.
- Route `cli-operator-surfaces` through a later proposal if its `expose`
  candidate is selected. The disposition record itself authorizes no product
  change.
- After successful Phase C only, resume the independently active M006
  `Cross-environment shadow proposal evidence` frontier, currently
  `ready_for_proposal`. Canonical M006 remains Active on
  `milestone/006-decision-facing-perception-readiness` with action policy
  “proposals may contain movement intent; applied vehicle control remains zero
  for the entire milestone.” No M006 artifact changes in this M007 unit.

## Cumulative PR Identity

| Field | Value |
| --- | --- |
| Cumulative PR | [#81](https://github.com/GeorgeLuo/auto-driving/pull/81) |
| Base | `main` |
| Head | `milestone/007-cli-operator-usability` |
| Rejected review head | `ee2e3056f77bee9a4511877829eb9c46b52d0aa2` |
| Restore head | `9f758d9927d8b870b1d3d2219441fd7410d64b47` |
| Phase A state | Draft and unmerged; body reconciled to this requalified judgment and exact final validation |
| Readiness owner | Phase C marks #81 ready only after the Phase B terminal handoff commit reaches the milestone tip |
| Acceptance | Independent exact-head whole-milestone review in Phase C; only acceptance permits merge to `main` and tag `milestone-007` |

Child proposal and implementation PRs target the milestone branch. Only #81
targets `main`.

## References

- Milestone plan: [plan.md](plan.md) · [plan.html](plan.html)
- Accepted closeout proposal: [#143](https://github.com/GeorgeLuo/auto-driving/pull/143)
  at merge `2ab7955b953f1d5863ee032db38271ca50d111a7`
- Accepted closeout requalification proposal: [#156](https://github.com/GeorgeLuo/auto-driving/pull/156)
  at merge `1eadd8abedf66d6575cf22f137219989343f1e88`
- Retained original Phase A implementation: [#144](https://github.com/GeorgeLuo/auto-driving/pull/144)
  at merge `ab72c7befdca8d69fe12f9d8c6d1ce554aeb5818` (not cumulative acceptance)
- Completed ledger: [completed.md](../completed.md)
- Durable operator guide:
  [cli-simulator-perception-journey.md](../../reference/cli-simulator-perception-journey.md)
- Accepted implementation units: [#84](https://github.com/GeorgeLuo/auto-driving/pull/84),
  [#88](https://github.com/GeorgeLuo/auto-driving/pull/88),
  [#100](https://github.com/GeorgeLuo/auto-driving/pull/100),
  [#107](https://github.com/GeorgeLuo/auto-driving/pull/107),
  [#122](https://github.com/GeorgeLuo/auto-driving/pull/122),
  [#138](https://github.com/GeorgeLuo/auto-driving/pull/138),
  [#146](https://github.com/GeorgeLuo/auto-driving/pull/146),
  [#154](https://github.com/GeorgeLuo/auto-driving/pull/154), and
  [#155](https://github.com/GeorgeLuo/auto-driving/pull/155)
- Tracked evidence: [live acceptance](evidence/live-cli-acceptance/),
  [scenario continuity](evidence/cli-scenario-continuity/),
  [journey coverage](evidence/cli-journey-coverage/),
  [CLI surface audit](evidence/cli-surface-audit/), and
  [capability disposition](evidence/capability-disposition/)
- Cumulative whole-milestone review: [#81](https://github.com/GeorgeLuo/auto-driving/pull/81)
