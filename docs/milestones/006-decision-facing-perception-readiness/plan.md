# Milestone 006 — Decision-Facing Perception Readiness

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/006-decision-facing-perception-readiness` |
| Cumulative PR | [#70](https://github.com/GeorgeLuo/auto-driving/pull/70) (draft until whole-milestone closeout) |
| Current frontier | Modular shadow action proposal foundation |
| Started | 2026-07-28 |
| Action policy | Proposals may contain movement intent; applied vehicle control remains zero for the entire milestone |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Establish one modular, replayable path from the decision cycle's current
observation, retained memory, pattern outputs, and projections to attributable
shadow action proposals. Prove the path with one memory-backed proposal
implementation and the same operator workflow against Chase and PiRacer inputs,
while runtime authority guarantees that no proposed movement is applied. This
tests whether the existing perception and memory contracts are usable by
decision logic; it does not establish autonomous movement safety or require a
prediction algorithm.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Primary demonstration | Chase or stationary PiRacer with supported obstruction evidence left or right of center and the shadow engine staged | Run the stage/automation/stream workflow, open the decision-view URL, then replay the recorded sequence | `avoid_recent_obstruction` proposes steering away from attributable retained evidence, the mixer selects it, the proposal progresses from fresh/retained through stale to inactive, the combined view explains the path, replay is deterministic, and applied autonomy control remains idle | M006-02, M006-03, M006-04, M006-05, M006-06, M006-07 |
| Stage and inspect shadow decision logic | Reachable or locally staged vehicle bundle | `./cli/automa vehicles update decision --id <vehicle> --engine shadow-proposals` then `./cli/automa vehicles info decision --id <vehicle>` | Human-readable output names the immutable decision inputs, enabled proposal plugins, mixer, output schema, shadow-only authority, and combined decision-view URL; `--json` exposes the complete machine contract | M006-01, M006-02, M006-03 |
| Observe current shadow proposals | Staged decision logic and an active automation worker | `./cli/automa vehicles automation run --id <vehicle>` then `./cli/automa vehicles stream decision --id <vehicle>` | Replacing latest-frame display and browser view show source frame, retained evidence overlay, proposal status and command, selected contribution, exact source references, and `applied=false` | M006-02, M006-03, M006-04, M006-05 |
| Replay a recorded decision sequence | Recorded observations with memory inputs or enough evidence to reproduce them | `./cli/automa vehicles decision apply --from-run <dir>` | Concise deterministic digest and per-frame proposal transitions; `--record` adds an exact-frame HTML review artifact, and no files are written otherwise | M006-02, M006-04, M006-05 |
| Exercise Chase without privileged input | Chase frontend active with the staged shadow engine | Run the stage/automation/stream workflow with `--id chase-sim-chaser` | Candidate proposals use camera-derived observation, memory, patterns, and projections only; evaluator shadow data remains isolated and proposed controls are not applied | M006-05, M006-06, M006-07 |
| Demonstrate the physical path | Stationary PiRacer in user mode, pointed at evidence supported by the packaged perception chain | Run the stage/automation/stream workflow with `--id piracer` | Live proposal output changes with attributable fresh/absent/stale memory evidence while drive mode stays `user` and pilot output stays zero | M006-04, M006-05, M006-06, M006-07 |

Command details may sharpen through accepted proposals, but completion must retain
one shared decision-data boundary, one reference proposal implementation, one
simple mixer, explicit shadow authority, and equivalent human-facing workflows
for simulator and physical vehicles.

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| Immutable, cycle-aligned decision data exposing observation, memory, patterns, projections, capabilities, timing, and prior applied-action context | Proposal plugins running perception, mutating memory, querying live stage state, or consuming evaluator truth |
| Modular proposal plugins with explicit availability, confidence, reason, proposed command, freshness, and source references | Complex consensus, learned mixing, proposal tournaments, or multiple policy implementations |
| One deterministic selector/mixer and an inspectable action-plan result | Applying proposed controls, collision-avoidance claims, or non-idle vehicle authority |
| One bounded `avoid_recent_obstruction` reference proposal that uses retained relative location and fails closed on absent, stale, incompatible, or incomplete evidence | Treating the diagnostic behavior as collision avoidance, semantic identity, metric localization, world-model reconstruction, or treating recurring evidence IDs as physical-object identity |
| Pattern and projection outputs available through the same source contract, including explicit unavailable/error states | Implementing or validating long-horizon prediction, trajectory planning, SLAM, or a metric motion model |
| Recorded replay, live streaming, and visual/structured evidence on Chase and PiRacer paths | New perception algorithms, VLM products, physical driving trials, or unrelated CLI expansion |
| A single canonical proposed vehicle-command shape with runtime-specific application adapters | Preserving parallel legacy action shapes or embedding DonkeyCar/Chase details in proposal plugins |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M006-01 | One immutable, cycle-aligned decision-data contract exposes current observation, retained memory, pattern outputs, projection outputs, vehicle capabilities, timing, and prior applied-action context with explicit unavailable/error states and no evaluator data | Unmet | First frontier |
| M006-02 | Independent proposal plugins consume only the decision-data contract and emit a bounded, serializable proposal containing identity, lifecycle status, confidence, reason, one canonical proposed command, freshness, assumptions, and exact evidence/pattern/projection references | Unmet | First frontier |
| M006-03 | One deterministic selector/mixer consumes the complete proposal set and emits an inspectable action plan with selected proposal or contributions, while a separate runtime authority result proves proposed and applied control cannot be confused | Unmet | First frontier |
| M006-04 | One packaged `avoid_recent_obstruction` proposal demonstrates left/right active steering, retained-fresh continuity, stale-to-inactive fallback, incompatible input, missing input, and plugin-error behavior without claiming navigation safety or semantic identity | Unmet | First frontier |
| M006-05 | Automa can stage, inspect, replay, and stream the decision path with concise default output, complete `--json` output, deterministic replay, latest-frame replacement, a combined frame/evidence/proposal/authority view, an opt-in exact-frame HTML artifact, and no default disk writes | Unmet | Requires accepted foundation |
| M006-06 | Tracked Chase and physical evidence exercise the same proposal contract and combined review view, showing exact source provenance, freshness transitions, proposal selection, proposed movement intent, and zero applied control | Unmet | Cross-environment evidence frontier |
| M006-07 | Chase evaluator/shadow state remains outside controller inputs, and PiRacer remains in user mode with zero pilot output throughout all milestone evidence | Unmet | Cross-environment evidence frontier |
| M006-08 | Closeout states whether the evidence justifies a later bounded movement or prediction milestone and preserves unresolved perception, identity, self-motion, command-model, and safety limits | Unmet | Closeout only |

## Current Delivery

### Current Frontier

**Modular shadow action proposal foundation**

- Workflow state: proposal_in_review
- Proposal branch: `m006/shadow-proposals-proposal`
- Implementation branch: `m006/shadow-proposals`
- Proposal path: `docs/milestones/006-decision-facing-perception-readiness/proposals/shadow-proposals.md`
- Review kind: Behavioral feature slice
- Review question: Can independent action-proposal plugins consume one immutable, cycle-aligned decision data source and produce attributable, replayable action plans while runtime authority guarantees that no proposed command is applied?
- Acceptance owner: Decision-data source, proposal/plan contracts, proposal runner, deterministic selector, shadow-authority result, and the `avoid_recent_obstruction` reference implementation
- Exit criteria affected: M006-01, M006-02, M006-03, M006-04
- Prerequisite: Milestone 005 closed at `milestone-005`; bounded evidence memory, exact provenance, replay, and idle Chase/Pi host paths remain available
- Milestone-level non-goal: Applied movement, new perception logic, prediction algorithms, semantic identity, metric geometry, complex mixing, evaluator input, or more than one reference proposal

### Next-Frontier Candidate

**Cross-environment shadow proposal evidence**

- Proposal branch: `m006/shadow-proposal-evidence-proposal`
- Implementation branch: `m006/shadow-proposal-evidence`
- Proposal path: `docs/milestones/006-decision-facing-perception-readiness/proposals/shadow-proposal-evidence.md`
- Review kind: Live or external evidence
- Review question: Does the staged `avoid_recent_obstruction` proposal produce deterministic, provenance-complete shadow action plans and one correlated visual explanation through the same operator workflow on recorded replay, Chase, and PiRacer inputs while applied control remains zero?
- Acceptance owner: Automa decision stage/info/apply/stream/view surfaces and tracked exact-frame Chase/Pi shadow evidence
- Exit criteria affected: M006-05, M006-06, M006-07
- Prerequisite: Modular shadow action proposal foundation accepted with replayable proposal and authority schemas
- Non-goals: Changing proposal semantics during evidence collection, selecting another proposal, applying movement, using privileged simulator state, or claiming physical navigation readiness

## Workflow History

| Frontier | State | Evidence |
| --- | --- | --- |
| Decision-facing fitness measures | ready_for_proposal | Activated after M005 cumulative PR #68 merged and mainline merge was tagged `milestone-005`. |
| Modular shadow action proposal foundation | ready_for_proposal | Plan revision: replaced the unstarted fitness-first frontier before any M006 proposal branch or artifact existed. |
| Modular shadow action proposal foundation | proposal_in_review | Started m006/shadow-proposals-proposal. |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| A shared decision-data source could degrade into an untyped mutable bag | Proposal dependencies and stage ownership would become implicit and order-sensitive | Require an immutable typed contract, stable component keys, explicit unavailable/error values, and mutation tests |
| Recurring evidence IDs do not establish physical-object identity | A proposal could steer toward a different object that reused a detector slot | Carry exact provenance and freshness, state the no-identity limit, and keep all milestone output shadow-only |
| Current bounded memory keeps the newest recurring record rather than a trajectory | Memory alone cannot support motion inference or validate long-horizon predictions | Prove latest-evidence proposal use now; retain pattern/projection slots and defer bounded temporal state to a reviewed later milestone |
| Apparent image motion combines object motion with vehicle self-motion | A later projection could attribute commanded camera motion to a perceived object | Include prior applied-action context in the source contract and require future prediction work to condition on it |
| `VehicleAction` and `AutonomyControl` currently express direction/throttle differently | Proposal implementations could accumulate runtime-specific adapters or contradictory command semantics | The first proposal must select one canonical proposed-command contract and isolate runtime conversion without compatibility branches |
| A mixer interface could invite premature consensus machinery | Framework complexity would grow before one proposal proves the data path | Implement one deterministic selector, keep contribution provenance, and explicitly defer Chase-style consensus |
| Packaged physical perception may not emit stable evidence for every placement | A structurally correct proposal may be inactive on narrow physical scenes | Use existing supported evidence, show active and fail-closed states honestly, and do not tune perception inside this milestone |
| Simulator success could be mistaken for physical readiness | Exact synthetic state and clean rendering do not represent carpet, optics, latency, or slip | Use Chase for contract/replay checks and require separate stationary Pi evidence without movement claims |

## Milestone Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-07-18 | Queue decision-facing perception readiness after memory | Physical parity retained the control with known side misses and clear-floor false positives; memory could proceed, movement could not |
| 2026-07-18 | Keep the existing perception architecture by default | Always-on observation, exact publication, plugins, and evidence-not-truth boundaries are already adequate |
| 2026-07-18 | Budget at most one decision implementation | Prevent an open policy or algorithm search while proving one concrete consumer of the stage contracts |
| 2026-07-28 | Activate M006 after M005 whole-milestone merge | Bounded evidence memory, provenance, replay, and idle host parity are closed and tagged |
| 2026-07-28 | Replace abstract fitness-first work before proposal authoring | An inspectable shadow proposal is a more direct test of decision-facing usability and still permits recorded fitness evidence without movement |
| 2026-07-28 | Give every proposal the same immutable decision-data source | Memory, patterns, and projections should be independently usable without proposal-specific engine wiring or shared mutation |
| 2026-07-28 | Carry prediction access but defer prediction algorithms | The architecture should not block prediction-backed proposals, while current memory does not yet justify trajectory claims |
| 2026-07-28 | Keep proposal generation modular and mixing simple | Independent plugins preserve experimentation; one deterministic selector avoids importing unvalidated Chase consensus mechanics |
| 2026-07-28 | Permit nonzero shadow intent but forbid application | Proposed and applied controls must be distinguishable before any movement milestone begins |

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- a completed-milestone ledger entry;
- tracked simulator and physical shadow-proposal evidence;
- a residual-risk statement covering perception uncertainty, identity, temporal
  history, self-motion, command conversion, and movement safety;
- a decision to activate, revise, or abandon one bounded movement or prediction
  pre-plan.
