# Milestone 007 — CLI Operator Usability

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/007-cli-operator-usability` |
| Cumulative PR | TBD |
| Current frontier | Simulator-to-perception CLI journey |
| Started | 2026-07-29 |
| Action policy | Observation-only; no applied vehicle movement |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Make the existing Automa CLI usable for one complete Chase operator journey:
start with a local simulator URL, determine which layer is available, establish
an observation-only automation worker, and open a frame-correlated browser view
of camera, observation, and perception output. Each failure must identify the
failed boundary and a concrete recovery action without requiring knowledge of
internal WebSocket paths, runtime files, or process topology.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Primary demonstration | Metrics UI intended at `http://localhost:5050`, with the Chase frontend either ready or recoverable | Use documented Automa commands to prepare Chase, discover `chase-sim-chaser`, start observation-only automation, and open its perception view | The browser shows a current camera frame and frame-matched observation/perception output; CLI output distinguishes simulator, vehicle, deployment, worker, and view state; no movement is applied | M007-01, M007-02, M007-03, M007-04 |
| Inspect current operator state | Any combination of online/offline simulator, connected/disconnected frontend, deployed/undeployed bundle, running/stopped worker, and available/unavailable view | Run the documented status/discovery commands in human or `--json` form | Every layer has one unambiguous state, ownership boundary, and next action; “active” never implies a running automation worker | M007-01, M007-04 |
| Recover a failed startup | Chase is reachable but frontend, scenario, capture contract, automation process, or perception view is not ready | Follow the exact recovery command emitted by the failing CLI surface, then retry | Recovery reaches the next state or fails at a newly named boundary without a generic timeout or collapsed “invalid identity or control reference” message | M007-02, M007-03, M007-04 |
| Validate the live journey | Current local Metrics UI and simulator deployment | Run the bounded live CLI acceptance procedure | One processed frame, a healthy loopback view, observation-only authority, and the expected human/JSON state are recorded without default history writes | M007-05 |

Command names and flags are frozen by the accepted proposal. The milestone
outcome is the bounded operator journey above, not a general redesign of every
Automa command.

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| Chase simulator preparation, vehicle discovery, automation deployment/worker state, and perception-view availability as distinct operator states | Redesigning unrelated perception experiments, decision surfaces, memory workflows, Pi deployment, or the entire command hierarchy |
| Human-readable defaults and complete machine-readable output for the bounded journey | A new interactive TUI, desktop application, or general service manager |
| HTTP/WS endpoint normalization and actionable frontend/scenario recovery | Supporting arbitrary remote browser authentication, public view hosting, or non-loopback view access |
| Observation-only camera/perception startup when evaluator-only reference data is unavailable | Weakening camera frame identity, allowing privileged evaluator data into controller inputs, or claiming shadow-evaluation evidence when its reference is absent |
| Exact startup diagnostics, bounded timeout semantics, and current operator documentation | Hiding external contract drift with retries, indefinite waits, or permissive malformed-capture acceptance |
| Deterministic tests plus one opt-in live acceptance unit against the current simulator contract | Making a browser or live simulator mandatory for the default deterministic test suite |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M007-01 | Automa exposes and documents one consistent operator state model that distinguishes simulator availability, simulator frontend readiness, vehicle discoverability, local automation deployment, worker liveness, and perception-view availability in concise human output and complete `--json` output | Unmet | First frontier |
| M007-02 | An operator starting from `http://localhost:5050` can prepare the supported Chase scenario and discover `chase-sim-chaser` without manually deriving `/ws/control`; disconnected, stale, wrong-game, and unavailable-camera states name the failed boundary and exact recovery | Unmet | First frontier |
| M007-03 | Observation-only automation can publish a current camera/perception browser view when sensor image and frame identity are valid even if evaluator-only control reference data is unavailable; workflows that require that reference fail closed with an explicit missing-reference status | Unmet | First frontier; current reproduction aborts the whole worker |
| M007-04 | Startup, status, and view commands use bounded operation-level timeout semantics, preserve stable human/JSON error categories, and provide current help/README examples for the complete journey and its recovery paths | Unmet | First frontier |
| M007-05 | A tracked live acceptance unit against the current local Metrics UI contract proves one observation-only processed frame, healthy loopback view, exact layer states, no applied movement, and no default recording; contract drift fails rather than skipping | Unmet | Next frontier after deterministic surfaces |
| M007-06 | Closeout confirms the primary demonstration, reconciles durable CLI documentation, and records any remaining external simulator, PiRacer, remote-view, or non-idle-control limits | Unmet | Closeout only |

## Current Delivery

### Current Frontier

**Simulator-to-perception CLI journey**

- Workflow state: ready_for_proposal
- Proposal branch: `m007/simulator-perception-cli-proposal`
- Implementation branch: `m007/simulator-perception-cli`
- Proposal path: `docs/milestones/007-cli-operator-usability/proposals/simulator-perception-cli.md`
- Review kind: Behavioral feature slice
- Review question: Can a Chase operator move from a local Metrics UI URL to a healthy observation-only perception browser view through discoverable Automa commands that distinguish every runtime layer and return exact, bounded recovery when the frontend, capture contract, worker, or view is unavailable?
- Acceptance owner: Automa simulator preparation, vehicle discovery, automation preflight/run/status, and perception-view operator surfaces over the Chase sensor-capture boundary
- Exit criteria affected: M007-01, M007-02, M007-03, M007-04
- Prerequisite: Milestone 005 mainline CLI and Chase adapter; no dependency on paused Milestone 006 implementation PR #80
- Non-goals: Broad CLI redesign, decision/memory feature work, PiRacer parity, remote view hosting, applied movement, or live acceptance evidence in the deterministic implementation unit

### Next-Frontier Candidate

**Live CLI operator acceptance**

- Proposal branch: `m007/live-cli-acceptance-proposal`
- Implementation branch: `m007/live-cli-acceptance`
- Proposal path: `docs/milestones/007-cli-operator-usability/proposals/live-cli-acceptance.md`
- Review kind: Live or external evidence
- Review question: Does the accepted simulator-to-perception CLI journey work end to end against the current local Metrics UI deployment with one processed observation-only frame, a healthy browser view, truthful layer states, no applied movement, and no default recording?
- Acceptance owner: Bounded live Chase operator procedure and tracked machine/human acceptance evidence
- Exit criteria affected: M007-05
- Prerequisite: Simulator-to-perception CLI journey accepted with deterministic contract coverage
- Non-goals: Product repair, PiRacer evidence, long-duration soak testing, performance qualification, non-idle control, or milestone closeout

## Workflow History

| Frontier | State | Evidence |
| --- | --- | --- |
| Simulator-to-perception CLI journey | ready_for_proposal | Activated as an operator-approved ad-hoc usability milestone while M006 implementation PR #80 is paused; separate branch and review-unit topology prevent scope leakage. |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| Metrics UI may evolve independently of this repository | Unit fixtures can remain green while live atomic-capture payloads drift | Freeze the consumed sensor/reference distinction, add reference-less fixtures, and require a non-skipping opt-in live contract unit |
| A browser tab can be visibly open before its Play WebSocket role is registered | A one-second probe can report a false unavailable state with no useful recovery | Distinguish server/frontend/game/camera states and use one bounded operation deadline with exact recovery |
| Existing “active” terminology is naturally read as “automation running” | Operators can misdiagnose a stopped worker or stale view as a healthy Automa vehicle | Define the state vocabulary once and make every relevant human/JSON surface use it consistently |
| Evaluator reference data is useful for scoring but is not sensor input | Requiring it for camera capture prevents legitimate observation-only perception; accepting malformed identity would weaken correlation | Validate sensor identity independently, model evaluator reference as optional/unavailable, and keep reference-required scoring fail-closed |
| Browser opening is platform-dependent | An otherwise healthy worker could be reported failed because the OS cannot launch a browser | Keep view health authoritative; browser launch is explicit and reports its own non-fatal result and URL |

## Milestone Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-07-29 | Activate M007 as a narrow ad-hoc milestone while M006 PR #80 is paused | The operator journey exposed cross-cutting CLI and live-contract failures that do not fit M006’s accepted decision-surface review question |
| 2026-07-29 | Keep proposal and implementation in separate PRs while using one conversation and reviewer | Conversation continuity is useful, but proposal acceptance must remain an explicit merge receipt before implementation starts |
| 2026-07-29 | Bound the first journey to local Chase and observation-only perception | This closes the reproduced usability failure without importing Pi deployment, remote hosting, decision work, or movement authority |
| 2026-07-29 | Treat evaluator control reference as optional for sensor-only perception and mandatory only for reference-dependent evidence | Camera/frame identity and evaluator scoring are different trust boundaries and should not make each other falsely unavailable |

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- a completed-milestone ledger entry;
- a durable operator guide for the supported simulator-to-perception journey;
- tracked live acceptance evidence against the current Metrics UI contract;
- a residual-risk statement covering external contract drift, PiRacer parity,
  browser launch, remote views, and movement authority.
