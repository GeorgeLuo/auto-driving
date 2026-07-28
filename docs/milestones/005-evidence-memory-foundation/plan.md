# Milestone 005 — Evidence Memory Foundation

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/005-evidence-memory-foundation` (active; created from `main` after #58) |
| Cumulative PR | TBD — transitional closeout delta from the milestone branch to `main`; whole-milestone judgment uses this plan and baseline |
| Current frontier | Milestone closeout |
| Contract baseline | `22cfff9` — M005 work through PR #50, before the compact contract |
| Grandfathered PRs | #57 (accepted evidence unit), #58 (contract migration); both retain their existing `main` targets |
| Cutover | #57 merged to `main`; #58 recorded its accepted result and established the remaining conflict frontier; the milestone branch was created from resulting `main` |
| Started | 2026-07-15 |
| Action policy | Idle / no movement for the entire milestone |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Give the decision cycle bounded, inspectable continuity across frames by
retaining attributed observation evidence—without claiming a complete world
model, changing perception semantics, or granting movement authority.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Stage memory | Known vehicle | `vehicles update memory --id <id> --implementation bounded_evidence` | Activation present; stage loadable | M005-04, M005-05 |
| Inspect / stream memory | Active memory stage | `vehicles stream memory --id <id>` / Memory map | Health, epoch, retained keys visible | M005-05, M005-06 |
| Reset memory | Running host with memory | `vehicles memory reset --id <id>` | Empty state with new epoch / reset count | M005-03, M005-05 |
| Replay observations | Observation sequence file | `vehicles memory replay <sequence> --id <id>` | Stable digest; bounded end state | M005-06, M005-08 |
| Record provenance | Explicit `--record` | Replay or lifecycle check with `--record` | Bounded provenance extract; no default history | M005-07, M005-12 |
| Lifecycle check (Pi) | Stationary Pi, live stage | `vehicles memory check --id <picar> --record` | Present / dropout / expiry / reset without movement | M005-09, M005-12 |
| Lifecycle check (Chase) | Chase sim + observe-only automation | `vehicles memory check --id chase-sim-chaser --record` | Alignment, retention, max-age, reset without candidate movement | M005-09–M005-12 |

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| Typed memory values, activation, host wiring, bounded evidence ledger | Semantic recognition, metric world models, track-ID identity claims |
| Operator stage / inspect / stream / reset / replay / opt-in record | Default disk history or always-on recording |
| Simulator and physical lifecycle evidence while action is idle | Non-idle movement authority or action policies |
| Framework-owned bounds, failure isolation, plugin-safe identity | Open multi-candidate perception search |
| Closeout judgment when criteria are met | Activating milestone 006 before 005 closeout |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M005-01 | Stable memory input and output types replace public `Any` at the decision-cycle boundary without encoding a concrete mapping algorithm | Met | Typed `MemorySnapshot` / activation contracts |
| M005-02 | Observation, memory, patterns, projections, and action have distinct documented meanings and stage ownership | Met | Stage meaning and idle action policy |
| M005-03 | Memory state has finite capacity and age, deterministic serialization, explicit reset, attributable provenance, and isolated failure behavior | Met | Core bounds, detach, plugin-safe IDs in #52; replay artifact bounds and isolated failure behavior in #53 |
| M005-04 | At least one simple implementation retains and expires structured evidence without claiming semantic or metric world truth | Met | `BoundedEvidenceLedger` |
| M005-05 | Same activation and lifecycle run through local Chase and onboard Donkey hosts; neither receives privileged simulator map state | Met | Load / update / status / reset paths on both hosts |
| M005-06 | Automa can stage, inspect, run, stream latest memory, replay a sequence, and reset with concise human and complete machine output | Met | Stage / inspect / stream / reset / replay landed |
| M005-07 | Default execution writes no logs, frames, or memory history; recording is explicit and bounded | Met | Defaults write nothing; #53 enforces opt-in replay frame/byte ceilings |
| M005-08 | Deterministic tests cover recurrence, dropout, conflict, expiry, capacity, reset, failure, and replay, including long-sequence boundedness | Met | PR #64 accepted the deterministic same-slot conflict contract, adversarial matrix, and per-prefix replay proof |
| M005-09 | One live Chase shadow check and one recorded non-moving Pi present/dropout/expiry/reset check verify equivalent stage behavior while the rewritten engine emits zero movement | Met | Pi lifecycle is recorded; #57 adds a guided Chase extract with `max_age_expiry` passing after 1,133 ms against a 1,000 ms bound, `reset_used=false`, and zero unapplied candidate control |
| M005-10 | During the simulator check, Chase’s built-in decision model retains movement authority, the rewrite runs observe-only, and candidate/reference results align by simulator frame identity | Met | Atomic evaluator path and tracked guided run |
| M005-11 | Simulator debug, map, and reference-decision state are evaluator-only and absent from rewritten controller inputs and retained memory provenance | Met | By design/wiring; re-asserted on check path |
| M005-12 | Tracked simulator and physical reviews visibly trace retained image-space evidence to exact perception source frames and regions; distinguish current from retained/expired; never treat stale coordinates as current geometry | Met | Physical and Chase provenance extracts tracked |
| M005-13 | Closeout states what memory representation proved useful, what remains unverified, and whether later pattern or action work is justified | Blocked | Requires all other criteria `Met` |

## Current Delivery

### Current Frontier

**Milestone closeout**

- Workflow state: proposal_in_review
- Proposal branch: `m005/closeout-proposal` (planned; not opened)
- Implementation branch: `m005/closeout` (planned; not opened)
- Proposal path: `docs/milestones/005-evidence-memory-foundation/proposals/closeout.md`
- Review kind: Milestone closeout
- Review question: Is milestone 005 complete as a whole—every exit criterion Met, completion usage supported, residual risk stated—and should the 006 pre-plan be activated, revised, or abandoned?
- Acceptance owner: `closeout.md` plus the cumulative milestone PR judgment against exit criteria and completion usage
- Exit criteria affected: M005-13
- Prerequisite: Every exit criterion Met, including the current conflicting-evidence contract
- Milestone-level non-goal: New feature implementation under the closeout PR; reopening max-age scoring design; activating 006 before closeout acceptance

### Next-Frontier Candidate

**None**

- Reason: No post-closeout frontier is reviewed.
- Revisit when: Milestone closeout decides whether to activate milestone 006.

## Workflow History

| Frontier | State | Evidence |
| --- | --- | --- |
| Conflicting evidence semantics | ready_for_proposal | #58 froze the minimal frontier contract; draft implementation PR #59 / `m005/conflicting-evidence` exists as a pre-gate exception and remains blocked until an independent proposal is accepted |
| Conflicting evidence semantics | proposal_in_review | Proposal PR #61 opened for independent conflict-semantics review. |
| Conflicting evidence semantics | ready_for_implementation | Proposal PR #61 accepted at 13b73f45958a50bff8aea5e2789b9052234604cb. |
| Conflicting evidence semantics | implementation_in_review | Started m005/conflicting-evidence. |
| Conflicting evidence semantics | accepted | Implementation PR #64 merged at 8d4772ba45575b6e0a3b73fdb08656d0c0dcccbd. |
| Milestone closeout | ready_for_proposal | Promoted after implementation PR #64. |
| Milestone closeout | proposal_in_review | Started m005/closeout-proposal. |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |
| Baseline #34–#50 (`22cfff9`) | Are the pre-contract M005 memory foundation, operator paths, Pi lifecycle proof, and integration-branch decision accepted as historical starting state? | Accepted before compact-contract adoption | M005-01–M005-12 at the statuses recorded above | Mainline history through `22cfff9`; tracked Pi and replay evidence referenced by the criteria |
| #51 | Is Chase retained evidence aligned to exact simulator frames while movement remains external and evaluator state isolated? | Accepted | M005-09, M005-10, M005-11, M005-12 | `evidence/chase-shadow-memory/` |
| #52 | Can callers mutate, enlarge, collide, or weaken activated memory bounds? | Accepted | M005-03, M005-08 | Deterministic bounds/detach/identity tests |
| #53 | Can operators treat a recorded replay extract as bounded and fail-closed, and can a live Chase memory probe be trusted only when the automation worker is fresh? | Accepted | M005-03, M005-07, M005-08 | Deterministic record/probe/once-exit tests |
| #57 | Can a guided live Chase run prove max-age expiry without reset while generation identity, capacity causality, zero unapplied control, and exact provenance hold? | Accepted | M005-08, M005-09 | `evidence/chase-max-age-expiry/`; 73 focused and 353 full deterministic tests |
| #64 | Does the bounded evidence ledger handle contradictory attributed evidence, same-slot recurrence, missing evidence, and structurally incompatible updates deterministically without silently claiming semantic truth? | Accepted | M005-08 | Deterministic conflict-policy matrix and per-prefix replay proof; 85 focused tests |

The baseline row is the explicit adoption boundary; post-baseline review units
remain one row per merged PR.

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| Process identity for Chase live probe relies on host command inspection | Probe may be unavailable or spoofable on unsupported hosts | Fail closed; document limitation |
| Memory is process-local by default | Restart continuity is not guaranteed | Explicit milestone non-goal |
| Metrics UI atomic capture remains an external dependency for Chase evidence | Capture quality depends on sibling repository | Keep dependency until auto-driving no longer needs contract adjustment |
| Historical 005 review units targeted `main` rather than a milestone integration branch | The final cumulative PR is a remaining-work delta rather than the literal implementation history | Use baseline `22cfff9`; after grandfathered #58 merges, create the milestone branch for conflict closure and closeout |

## Milestone Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-07-15 | Memory is retained attributed evidence, not a world model | Perception emits uncertain, coordinate-scoped claims; stronger models must be earned |
| 2026-07-15 | Action stays idle for the entire milestone | Evaluate memory lifecycle without movement safety coupling |
| 2026-07-15 | Memory is bounded and process-local by default | Persistence costs are unjustified before useful in-memory representation exists |
| 2026-07-15 | Do not promote perception track IDs into durable identity | Track groups are run-local motion evidence without proven continuity |
| 2026-07-15 | Same memory interface on Chase and Donkey hosts | Environment-specific truth would undermine sim-before-Pi testing |
| 2026-07-18 | Require visual provenance on Pi and Chase | Counts alone do not prove memory derives from real perception |
| 2026-07-18 | Chase built-in model is shadow reference only | Privileged state must never become candidate input |
| 2026-07-20 | Reopen package 5 after validation review | Checks reported success without proving live lifecycle and exact-frame claims |
| 2026-07-22 | Accept atomic Chase shadow path; keep Metrics UI dependency | Proven alignment/retention/reset; still need max-age and remaining integrity work |
| 2026-07-23 | Co-review next frontier on the current unmerged PR | Human attention can accept current code and next scope in one pass |
| 2026-07-24 | Adopt compact plan ownership and review-unit branch model | Remove duplicated status surfaces; minimize post-merge plan sync |
| 2026-07-24 | Require a minimal pre-implementation acceptance contract for next-frontier candidates | Selecting “what’s next” must freeze question, owner, exit criteria, and non-goals before a branch is opened; name stubs are not candidates |
| 2026-07-26 | Cut over M005 after grandfathered PR #57 and contract PR #58 merge in that order | Avoid retargeting #57 or merging conflicting hand-authored HTML; #58 becomes the canonical plan migration and the remaining review units use the milestone branch |
| 2026-07-27 | Accept #57 and keep conflicting evidence ahead of closeout | The live Chase causal proof closes M005-09, but its accepted contract explicitly leaves M005-08 open; closeout cannot conceal an unspecified conflict policy |
| 2026-07-27 | Open independent proposal for conflicting-evidence before implementation | Proposal gate requires reviewed contract, matrix, and validation plan before implementation may proceed under the gate |
| 2026-07-27 | Separate proposal review from implementation review | A lower model authors a proposal, the reviewer accepts it, and only then may implementation begin; machine state and history must expose the handoff |

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- completed-milestone ledger update;
- final residual-risk statement;
- decision to activate, revise, or abandon the 006 pre-plan.
