# Milestone 006 — Decision-Facing Perception Readiness

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/006-decision-facing-perception-readiness` |
| Cumulative PR | TBD |
| Current frontier | Decision-facing fitness measures |
| Started | 2026-07-28 |
| Action policy | Idle / no movement for the entire milestone |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Decide whether packaged perception evidence is reliable enough for one named,
constrained decision experiment, or qualify at most one bounded upgrade under
the existing perception contract. The result must be reproducible on recorded
evidence and visually inspectable without granting movement authority.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Collect physical control evidence | Stationary PiRacer with packaged perception active | `./cli/automa vehicles perception check --id piracer --record` | Labeled, frame-matched check run with zero control | M006-01, M006-02, M006-05 |
| Score decision fitness | Recorded labeled sequence | `./cli/automa vehicles perception fitness --from-run <dir>` | Concise threshold verdict plus machine-readable measures and exact-frame references | M006-01, M006-02 |
| Exercise the same input boundary | Active Chase or recorded simulator sequence | `./cli/automa vehicles perception run --id chase-sim-chaser --record` then `./cli/automa vehicles perception fitness --from-run <dir>` | Simulator evidence is processed without controller access to map or evaluator truth | M006-02, M006-05 |
| Qualify one upgrade when earned | Control failed its declared gate | `./cli/automa vehicles perception qualify --from-check-run <dir> --candidate <id>` | Exactly one `promote` or `reject_keep_control` result on common frames | M006-03 |
| Verify a promoted physical path | Candidate was promoted and deployed | `./cli/automa vehicles perception viability --id piracer` | Cadence, freshness, inspection, and zero-control gates pass | M006-04, M006-06 |

Command details may sharpen through accepted proposals, but the workflow count
and their observable outcomes must not expand into an open candidate search.

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| Decision-facing fitness measures and thresholds for one named constrained policy class | Non-idle movement, collision-avoidance claims, or action-policy implementation |
| Packaged control scored first on labeled, exact-frame evidence | Multi-candidate tournaments or trying every lab experiment |
| At most one common-frame upgrade qualification if the control fails | Semantic recognition, VLM products, metric SLAM, or floor-plan reconstruction |
| Recorded-sequence evaluation usable with Pi and Chase capture paths | Privileged simulator map/evaluator state entering controller perception or memory |
| Pi viability and visual inspection if an upgrade is promoted | Rewriting perception, memory, or always-on publication contracts |
| A closeout sufficiency decision for the next milestone | Treating attractive overlays or desktop speed as adoption evidence |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M006-01 | One named constrained decision-policy class has a reviewed fitness contract with explicit measures, thresholds, labels, uncertainty treatment, and stop conditions | Unmet | First frontier |
| M006-02 | Automa scores the packaged control from bounded recorded evidence with concise human output, complete machine output, exact-frame references, deterministic replay, and no default disk writes | Unmet | Requires accepted fitness contract and implementation |
| M006-03 | The packaged control either passes its declared gate or exactly one upgrade attempt receives an explicit `promote` or `reject_keep_control` result on common frames | Unmet | Control must be scored before candidate selection |
| M006-04 | If an upgrade is promoted, its onboard Pi path passes cadence, freshness, inspectability, and zero-control viability gates; otherwise this criterion is met by recorded non-promotion | Unmet | Conditional on M006-03 |
| M006-05 | Tracked visual evidence demonstrates the scored representation on physical frames and exercises the same recorded-sequence boundary with Chase or controlled fixtures without controller access to privileged truth | Unmet | Requires exact-frame review artifacts |
| M006-06 | Closeout states whether a later shadow/non-idle decision milestone is justified, preserves residual limits, and confirms action remained idle throughout M006 | Unmet | Closeout only |

## Current Delivery

### Current Frontier

**Decision-facing fitness measures**

- Workflow state: `ready_for_proposal`
- Proposal branch: `m006/decision-fitness-proposal`
- Implementation branch: `m006/decision-fitness`
- Proposal path: `docs/milestones/006-decision-facing-perception-readiness/proposals/decision-fitness.md`
- Review kind: Decision-fitness contract
- Review question: Can one bounded, host-neutral fitness contract turn recorded perception checks into reproducible evidence about whether the packaged control can support a named constrained shadow-decision policy while action remains idle?
- Acceptance owner: Recorded-sequence fitness schema, scorer boundary, and Automa human/machine report contract
- Exit criteria affected: M006-01, M006-02
- Prerequisite: Milestone 005 closed at `milestone-005`; existing Pi check and Chase capture paths remain available
- Non-goals: Algorithm changes, candidate selection, movement, semantic detection, metric geometry, or a second evaluation framework

### Next-Frontier Candidate

**Control fitness evidence**

- Proposal branch: `m006/control-fitness-evidence-proposal`
- Implementation branch: `m006/control-fitness-evidence`
- Proposal path: `docs/milestones/006-decision-facing-perception-readiness/proposals/control-fitness-evidence.md`
- Review kind: Recorded control evaluation
- Review question: Does the packaged control satisfy the accepted decision-fitness gate on bounded exact-frame evidence?
- Acceptance owner: Tracked control report and exact-frame visual evidence
- Exit criteria affected: M006-02, M006-03, M006-05
- Prerequisite: Decision-facing fitness implementation accepted
- Non-goals: Selecting or implementing an upgrade before the control verdict, live movement, or expanding the candidate budget

## Workflow History

| Frontier | State | Evidence |
| --- | --- | --- |
| Decision-facing fitness measures | ready_for_proposal | Activated after M005 cumulative PR #68 merged and mainline merge was tagged `milestone-005`. |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| Existing physical check runs may not cover the declared distance, placement, and lighting band | A threshold could appear reliable only because the evidence is narrow | The first proposal must name minimum coverage and fail closed when evidence is insufficient |
| A scorer could overfit one run or encode Pi-only directory conventions | Results would not replay or transfer through the shared vehicle boundary | Keep the evaluation input bounded and host-neutral; require held-out or repeated evidence where the proposal justifies it |
| Simulator scenes do not reproduce carpet, lighting, or camera optics | Chase success could be mistaken for physical readiness | Use Chase for contract and causal-path checks; physical frames remain required for the fitness verdict |
| Confidence values are not yet calibrated probabilities | Thresholds may imply unsupported certainty | Treat confidence as algorithm evidence and validate observable behavior rather than probabilistic truth |

## Milestone Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-07-18 | Queue decision-facing perception readiness after memory | Physical parity retained the control with known side misses and clear-floor false positives; memory could proceed, movement could not |
| 2026-07-18 | Keep the existing perception architecture by default | Always-on observation, exact publication, plugins, and evidence-not-truth boundaries are already adequate |
| 2026-07-18 | Budget at most one upgrade attempt | Prevent an open algorithm search while allowing one evidence-driven correction |
| 2026-07-28 | Activate M006 after M005 whole-milestone merge | Bounded evidence memory, provenance, replay, and idle host parity are now closed and tagged |
| 2026-07-28 | Make fitness host-neutral but physical-grounded | Simulator and Pi should exercise one recorded-sequence contract, while only physical evidence can establish physical readiness |
| 2026-07-28 | Require proposal review before fitness implementation | Measures and thresholds define success and must not be invented by implementation |

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- a completed-milestone ledger entry;
- a final control-or-upgrade decision and residual-risk statement;
- a decision to activate, revise, or abandon one later shadow/non-idle decision pre-plan.
