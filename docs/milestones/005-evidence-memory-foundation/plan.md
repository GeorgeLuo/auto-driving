# Milestone 005 — Evidence Memory Foundation

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/005-evidence-memory-foundation` (to be created for new review units) |
| Cumulative PR | TBD — open draft from milestone branch to `main` when topology is active |
| Current frontier | Live Chase max-age expiry — [PR #57](https://github.com/GeorgeLuo/auto-driving/pull/57) |
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
| M005-03 | Memory state has finite capacity and age, deterministic serialization, explicit reset, attributable provenance, and isolated failure behavior | Partial | Core bounds, detach, plugin-safe IDs in #52; replay artifact bounds in #53 |
| M005-04 | At least one simple implementation retains and expires structured evidence without claiming semantic or metric world truth | Met | `BoundedEvidenceLedger` |
| M005-05 | Same activation and lifecycle run through local Chase and onboard Donkey hosts; neither receives privileged simulator map state | Met | Load / update / status / reset paths on both hosts |
| M005-06 | Automa can stage, inspect, run, stream latest memory, replay a sequence, and reset with concise human and complete machine output | Met | Stage / inspect / stream / reset / replay landed |
| M005-07 | Default execution writes no logs, frames, or memory history; recording is explicit and bounded | Partial | Defaults write nothing; #53 enforces opt-in replay frame/byte ceilings |
| M005-08 | Deterministic tests cover recurrence, dropout, conflict, expiry, capacity, reset, failure, and replay, including long-sequence boundedness | Partial | #52/#53 size, collision, long-sequence, stale-worker, max-age scoring; broader conflict scenarios may expand later |
| M005-09 | One live Chase shadow check and one recorded non-moving Pi present/dropout/expiry/reset check verify equivalent stage behavior while the rewritten engine emits zero movement | Partial | Pi validated; Chase retention/alignment/reset accepted (#51); max-age expiry scoring under review (#57); live extract still required for full claim |
| M005-10 | During the simulator check, Chase’s built-in decision model retains movement authority, the rewrite runs observe-only, and candidate/reference results align by simulator frame identity | Met | Atomic evaluator path and tracked guided run |
| M005-11 | Simulator debug, map, and reference-decision state are evaluator-only and absent from rewritten controller inputs and retained memory provenance | Met | By design/wiring; re-asserted on check path |
| M005-12 | Tracked simulator and physical reviews visibly trace retained image-space evidence to exact perception source frames and regions; distinguish current from retained/expired; never treat stale coordinates as current geometry | Met | Physical and Chase provenance extracts tracked |
| M005-13 | Closeout states what memory representation proved useful, what remains unverified, and whether later pattern or action work is justified | Blocked | Requires all other criteria `Met` |

## Current Delivery

### Current Frontier

**Live Chase max-age expiry**

- PR: [#57](https://github.com/GeorgeLuo/auto-driving/pull/57)
- Branch: `agent/chase-live-max-age-expiry` *(historical topology targets `main`; new units should use `m005/…` → milestone branch)*
- Review kind: Live external evidence + deterministic scoring path
- Review question: Can a guided live Chase run show a retained key surviving dropout and then disappearing under configured `max_age_ms` without reset, while observe-only / zero movement and exact source-frame provenance hold?
- Exit criteria affected: M005-08, M005-09
- Prerequisite: Package 5B (#53) accepted
- Milestone-level non-goal: Package 6 closeout; Pi re-proof; 5A/5B redesign

### Next-Frontier Candidate

**Milestone closeout**

- Review kind: Milestone closeout
- Question: Is milestone 005 complete as a whole, and should the 006 pre-plan be activated?
- Prerequisite: Every exit criterion `Met`, including live Chase max-age evidence
- Non-goal: New feature implementation under the closeout PR

Not started. Scope may change based on evidence from the current frontier.

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |
| #51 | Is Chase retained evidence aligned to exact simulator frames while movement remains external and evaluator state isolated? | Accepted | M005-09, M005-10, M005-11, M005-12 | `evidence/chase-shadow-memory/` |
| #52 | Can callers mutate, enlarge, collide, or weaken activated memory bounds? | Accepted | M005-03, M005-08 | Deterministic bounds/detach/identity tests |
| #53 | Can operators treat a recorded replay extract as bounded and fail-closed, and can a live Chase memory probe be trusted only when the automation worker is fresh? | Accepted | M005-03, M005-07, M005-08 | Deterministic record/probe/once-exit tests |

Earlier stage/inspect/reset/replay/record and Pi lifecycle work is accepted in prior merges; this ledger focuses on review units with durable PR references for the validation-repair arc. Expand rows as new units merge under this contract.

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| Live Chase max-age has not yet been proven with a tracked guided extract under the new scoring path | M005-09 remains partial | Current frontier #57 + operator live run |
| Process identity for Chase live probe relies on host command inspection | Probe may be unavailable or spoofable on unsupported hosts | Fail closed; document limitation |
| Memory is process-local by default | Restart continuity is not guaranteed | Explicit milestone non-goal |
| Metrics UI atomic capture remains an external dependency for Chase evidence | Capture quality depends on sibling repository | Keep dependency until auto-driving no longer needs contract adjustment |
| Historical 005 review units targeted `main` rather than a milestone integration branch | Cumulative whole-milestone review is harder | Adopt milestone branch topology for remaining units and closeout |

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

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- completed-milestone ledger update;
- final residual-risk statement;
- decision to activate, revise, or abandon the 006 pre-plan.
