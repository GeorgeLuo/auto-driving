# Milestone 007 — CLI Operator Usability

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/007-cli-operator-usability` |
| Cumulative PR | [#81](https://github.com/GeorgeLuo/auto-driving/pull/81) (draft until whole-milestone closeout) |
| Current frontier | Live CLI operator acceptance |
| Started | 2026-07-29 |
| Action policy | Observation-only; no applied vehicle movement |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Make the Automa CLI a dependable and inspectable product surface. First, make
one complete Chase operator journey usable from a local simulator URL through
an observation-only, frame-correlated browser view without commandeering the
current simulator session. Then measure which owned source branches named CLI
commands and realistic multi-command journeys actually execute, audit every CLI
leaf into an explicit usage pattern, and classify production capabilities those
journeys do not reach so later work can expose, retain, or remove them through
evidence rather than accumulation. Failures must identify the owning boundary
without requiring knowledge of internal WebSocket paths, runtime files, or
process topology; coverage remains an investigative signal rather than a claim
that executed code is correct or unexecuted code is dead.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Primary demonstration | Metrics UI intended at `http://localhost:5050` already exposes a Chase-compatible vehicle and front camera in any scenario/control/playback state; no pre-existing Automa bundle or worker is required | Run the exact command sequence below to passively inspect the current vehicle, stage packaged perception, start observation-only automation, open and inspect its perception view, then stop the worker | The browser shows a current camera frame and frame-matched observation/perception output; CLI output distinguishes simulator, vehicle, deployment, worker, and view state; scenario, playback, control source, and input remain unchanged; cleanup leaves no worker running | M007-01, M007-02, M007-03, M007-04 |
| Inspect current operator state | Any combination of online/offline simulator, connected/disconnected frontend, deployed/undeployed bundle, running/stopped worker, and available/unavailable view | Run the documented status/discovery commands in human or `--json` form | Every layer has one unambiguous state, ownership boundary, and next action; “active” never implies a running automation worker | M007-01, M007-04 |
| Recover a failed startup | Chase is reachable but frontend, passive capture capability, capture contract, automation process, or perception view is not ready | Follow the exact recovery emitted by the failing CLI surface, then retry; simulator reconfiguration remains an explicit opt-in rather than a hidden recovery | Recovery reaches the next state or names the missing external simulator capability and minimal requested change without a generic timeout, silent scenario selection, or collapsed “invalid identity or control reference” message | M007-02, M007-03, M007-04 |
| Validate the live journey | Current local Metrics UI and simulator deployment | Run the bounded live CLI acceptance procedure | One processed frame, a healthy loopback view, observation-only authority, preserved scenario/playback/control/input state, and the expected human/JSON state are recorded without default history writes | M007-05 |
| Trace source use by CLI journey | Clean repository state with the existing branch-aware owned-Python coverage configuration | Record named CLI commands and multi-command journeys with stable contexts, combine foreground and Python subprocess/background-worker data, and compare the result with a measured CLI bootstrap/import baseline | A reproducible report attributes executed statements and branches to command/journey contexts, separates shared import/bootstrap cost from command-specific behavior, preserves source revision and command identity, and imposes no aggregate percentage gate | M007-07 |
| Audit the complete CLI surface | Parser/help surface and journey-coverage collector accepted | Enumerate every public CLI leaf and map it to realistic operator/developer usage patterns, prerequisites, side effects, safety class, expected output, owning boundary, and deterministic or live validation | Every leaf is assigned to at least one named realistic pattern or explicitly classified unsupported/deprecated; hazardous or external leaves are documented without being run unsafely | M007-08 |
| Disposition code outside CLI journeys | Accepted leaf inventory, realistic usage patterns, aggregate journey coverage, and existing deterministic-test coverage | Group unexecuted owned source regions by capability and owner, then review CLI reachability, other entrypoints, tests, dynamic loading, and platform constraints | Every uncovered capability group is flagged as a candidate to expose through CLI, retain as non-CLI with an explicit owner/reason, or remove; no feature or deletion is authorized solely by a coverage percentage | M007-09 |

### Primary Demonstration Command Sequence

Run from the repository root:

```sh
# Passively discover the currently exposed vehicle and inspect every layer.
./cli/automa vehicles status --chase-url http://localhost:5050

# Idempotently stage the packaged perception path and safe idle decision bundle.
./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --algorithm lightweight_observer

# Start observation-only inference and open the healthy browser view.
./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
  --open-view

# Gate the running state after inspecting the opened frame/perception view.
./cli/automa vehicles status --id chase-sim-chaser

# Bounded cleanup: leave no automation worker running.
./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles status --id chase-sim-chaser
```

The first post-start status must report a deployed bundle, running
observation-only worker, available current-generation view, and no applied
control. The final status must report the same deployment, a stopped worker,
and no available current-generation view. Browser inspection between those
checks must show a current camera frame and frame-matched observation and
perception output.

No simulator preparation command is part of the primary demonstration. Status,
staging, observation-only automation, and cleanup must not select a scenario,
change playback, take control, or send an idle/control input. If the current
Metrics UI cannot expose a passive camera capture, the CLI must report that
external capability gap and the minimal requested simulator change rather than
calling `simulators ensure` or mutating the session implicitly.

Command names and flags in the primary demonstration are frozen by the accepted
proposal. The later full-surface audit inventories the existing CLI and its
source reachability before proposing redesign, new exposure, or deletion; it
does not retroactively widen the accepted primary journey.

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| Passive attachment to a currently exposed Chase-compatible vehicle, plus explicit simulator preparation as an operator-chosen recovery | Redesigning unrelated perception, decision, memory, Pi, or command-hierarchy behavior before reachability and usage evidence is reviewed |
| Human-readable defaults and complete machine-readable output for the bounded journey | A new interactive TUI, desktop application, or general service manager |
| HTTP/WS endpoint normalization, passive-capability reporting, and actionable frontend recovery without hidden reconfiguration | Supporting arbitrary remote browser authentication, public view hosting, or non-loopback view access |
| Observation-only camera/perception startup that preserves simulator scenario, playback, control source, and input when evaluator-only reference data is unavailable | Weakening camera frame identity, allowing privileged evaluator data into controller inputs, or claiming shadow-evaluation evidence when its reference is absent |
| Exact startup diagnostics, bounded timeout semantics, and current operator documentation | Hiding external contract drift with retries, indefinite waits, or permissive malformed-capture acceptance |
| Deterministic tests plus one opt-in live acceptance unit against the current simulator contract | Making a browser or live simulator mandatory for the default deterministic test suite |
| Branch-aware, subprocess-complete owned-Python coverage attributed to named CLI command and journey contexts | Treating import-time execution as feature use, measuring Metrics UI JavaScript in the Python report, or setting an arbitrary repository-wide coverage gate |
| Complete CLI-leaf inventory and realistic usage-pattern catalog, including prerequisites and side-effect/safety classification | Executing hardware-, movement-, destructive-, or external-state commands merely to increase coverage |
| Capability-level review of owned source not reached by declared CLI journeys | Automatically deleting uncovered code, exposing every internal primitive, or pre-authorizing feature/removal changes before separate review |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M007-01 | Automa exposes and documents one consistent operator state model that distinguishes simulator availability, simulator frontend readiness, vehicle discoverability, local automation deployment, worker liveness, and perception-view availability in concise human output and complete `--json` output | Met | Consistent simulator, vehicle, deployment, worker, view, and evaluator-reference state vocabulary plus shared next-step readiness gates in human and JSON CLI surfaces in PR #84 |
| M007-02 | An operator starting from `http://localhost:5050` can prepare the supported Chase scenario and discover `chase-sim-chaser` without manually deriving `/ws/control`; disconnected, stale, wrong-game, and unavailable-camera states name the failed boundary and exact recovery | Met | Local HTTP URL normalization, passive Chase discovery, explicit-only configured preparation, and exact frontend/game/camera/capability recovery in PR #84 |
| M007-03 | Observation-only automation can publish a current camera/perception browser view when sensor image and frame identity are valid even if evaluator-only control reference data is unavailable; workflows that require that reference fail closed with an explicit missing-reference status | Met | Passive observation-only sensor/perception startup preserves scenario/playback/control/input state, separates optional evaluator reference, and keeps reference-dependent operations fail-closed in PR #84 |
| M007-04 | Startup, status, and view commands use bounded operation-level timeout semantics, preserve stable human/JSON error categories, and provide current help/README examples for the complete journey and its recovery paths | Met | Bounded shared readiness deadlines, stable actionable error categories, cross-command gate agreement, open-view workflow, parser-valid cross-level help and recovery, README, and durable operator guide in PR #84 |
| M007-05 | A tracked live acceptance unit against the current local Metrics UI contract proves one observation-only processed frame, healthy loopback view, exact layer states, no applied movement, and no default recording; contract drift fails rather than skipping | Unmet | Current live evidence frontier |
| M007-06 | Closeout confirms the primary demonstration, reconciles durable CLI documentation, records the accepted journey-coverage and full-leaf audit outcomes, and states every retained/unexposed capability and remaining external simulator, PiRacer, remote-view, or non-idle-control limit | Unmet | Closeout only after M007-01 through M007-05 and M007-07 through M007-09 |
| M007-07 | A reproducible CLI journey-coverage collector attributes owned-Python statement and branch execution to named commands and multi-command journeys across foreground and Python subprocess/background-worker boundaries, separates bootstrap/import footprint from command-specific behavior, records exact source/command identity, and remains informational | Unmet | Next instrumentation frontier after live acceptance |
| M007-08 | A complete generated-and-reviewed inventory maps every public CLI leaf to realistic usage patterns, prerequisites, side effects, safety class, output contract, owning boundary, and deterministic/live validation status without requiring unsafe execution | Unmet | Follows the accepted coverage collector |
| M007-09 | Owned production code not reached by the declared CLI journey set is grouped by capability and reconciled with tests, other entrypoints, dynamic/platform paths, and ownership; every group is flagged to expose through CLI, retain with explicit reason, or remove through separately reviewed work | Unmet | Follows the accepted CLI-leaf and usage-pattern audit |

## Current Delivery

### Current Frontier

**Live CLI operator acceptance**

- Workflow state: proposal_amendment_in_review
- Proposal branch: `m007/live-cli-acceptance-proposal`
- Implementation branch: `m007/live-cli-acceptance`
- Proposal path: `docs/milestones/007-cli-operator-usability/proposals/live-cli-acceptance.md`
- Accepted proposal: [#86](https://github.com/GeorgeLuo/auto-driving/pull/86) at `cdb9e55f94293823dc5aae8e02356d16eed4eea2`
- Proposal amendment branch: `m007/amend-live-cli-acceptance-correlation`
- Proposal amendment path: `docs/milestones/007-cli-operator-usability/proposals/live-cli-acceptance-correlation-amendment.md`
- Review kind: Live or external evidence
- Review question: Does the accepted simulator-to-perception CLI journey work end to end against the current local Metrics UI deployment with one processed observation-only frame, a healthy browser view, truthful layer states, no applied movement, and no default recording?
- Acceptance owner: Bounded live Chase operator procedure and tracked machine/human acceptance evidence
- Exit criteria affected: M007-05
- Prerequisite: Simulator-to-perception CLI journey accepted with deterministic contract coverage
- Milestone-level non-goal: Product repair, PiRacer evidence, long-duration soak testing, performance qualification, non-idle control, or milestone closeout

### Next-Frontier Candidate

**CLI journey coverage foundation**

- Proposal branch: `m007/cli-journey-coverage-proposal`
- Implementation branch: `m007/cli-journey-coverage`
- Proposal path: `docs/milestones/007-cli-operator-usability/proposals/cli-journey-coverage.md`
- Review kind: Deterministic invariant closure
- Review question: Can a developer record reproducible branch-aware owned-Python coverage for named CLI commands and multi-command journeys across foreground and Python subprocess/background-worker execution while separating bootstrap/import footprint from command-specific behavior and avoiding false correctness or dead-code claims?
- Acceptance owner: Coverage session collector, command/context manifest, bootstrap subtraction, and versioned journey-coverage report schema built on `.coveragerc`
- Exit criteria affected: M007-07
- Prerequisite: M007-05 is `Met` and the existing subprocess-aware owned-code coverage baseline remains available
- Milestone-level non-goal: Exhaustive CLI-leaf audit, capability disposition, product deletion, new CLI features, Metrics UI JavaScript coverage, a numeric coverage gate, or a live simulator requirement

## Workflow History

| Frontier | State | Evidence |
| --- | --- | --- |
| Simulator-to-perception CLI journey | ready_for_proposal | Activated as an operator-approved ad-hoc usability milestone while M006 implementation PR #80 is paused; separate branch and review-unit topology prevent scope leakage. |
| Simulator-to-perception CLI journey | proposal_in_review | Started m007/simulator-perception-cli-proposal. |
| Simulator-to-perception CLI journey | ready_for_implementation | Proposal PR #82 accepted at 70d7419e3fc8bcd8e8483c16c8d061c04f86a0a9. |
| Simulator-to-perception CLI journey | implementation_in_review | Started m007/simulator-perception-cli. |
| Simulator-to-perception CLI journey | accepted | Implementation PR #84 merged at 6c6a4dc14a8d94770e737cff4f0e6a4f5aa7ae89. |
| Live CLI operator acceptance | ready_for_proposal | Promoted after implementation PR #84. |
| Live CLI operator acceptance | ready_for_proposal | Plan revision: queued milestone closeout as the reviewed successor to successful live acceptance; live scope and acceptance ownership are unchanged. |
| Live CLI operator acceptance | ready_for_proposal | Plan revision: expanded M007 through CLI journey coverage, complete leaf/usage-pattern audit, and evidence-based disposition of unexposed code; replaced premature closeout with the coverage foundation while preserving the live acceptance contract. |
| Live CLI operator acceptance | proposal_in_review | Started m007/live-cli-acceptance-proposal. |
| Live CLI operator acceptance | ready_for_implementation | Proposal PR #86 accepted at cdb9e55f94293823dc5aae8e02356d16eed4eea2. |
| Live CLI operator acceptance | proposal_amendment_in_review | Started proposal amendment m007/amend-live-cli-acceptance-correlation. |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |
| #84 | Can a Chase operator move from a local Metrics UI URL to a healthy observation-only perception browser view through discoverable Automa commands that distinguish every runtime layer and return exact, bounded recovery when the frontend, capture contract, worker, or view is unavailable? | Accepted | M007-01, M007-02, M007-03, M007-04 | Passive Chase simulator-to-perception CLI journey with aggregate layer status, shared sequential-readiness gates, HTTP/WS normalization, observation-only first-frame view startup, simulator-state preservation, exact capture/reference diagnostics, operation-level deadlines, cross-level help audit, and durable operator documentation in PR #84 |

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| Metrics UI may evolve independently of this repository | Unit fixtures can remain green while live atomic-capture payloads drift | Freeze the consumed sensor/reference distinction, add reference-less fixtures, and require a non-skipping opt-in live contract unit |
| A browser tab can be visibly open before its Play WebSocket role is registered | A one-second probe can report a false unavailable state with no useful recovery | Distinguish server/frontend/game/camera states and use one bounded operation deadline with exact recovery |
| Evaluator reference data is useful for scoring but is not sensor input | Requiring it for camera capture prevents legitimate observation-only perception; accepting malformed identity would weaken correlation | Validate sensor identity independently, model evaluator reference as optional/unavailable, and keep reference-required scoring fail-closed |
| Browser opening is platform-dependent | An otherwise healthy worker could be reported failed because the OS cannot launch a browser | Keep view health authoritative; browser launch is explicit and reports its own non-fatal result and URL |
| Eager CLI imports execute shared module top levels before a leaf handler runs | Naive per-command coverage overstates feature use and makes unrelated modules look journey-owned | Record a bootstrap/help context, preserve per-command contexts, and report shared/bootstrap execution separately from command-specific lines and branches |
| Coverage absence is not proof that code is dead | Hardware paths, dynamic plugins, libraries, and non-CLI entrypoints could be removed or exposed incorrectly | Reconcile uncovered regions with tests, entrypoints, dynamic loading, platform constraints, and an explicit owner before disposition |
| Running every CLI leaf can be unsafe or environment-dependent | A coverage target could encourage movement, destructive operations, hardware access, or misleading skips | Inventory every leaf, but execute only declared safe patterns; record prerequisites and non-executed live/hazardous classifications explicitly |

## Milestone Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-07-29 | Activate M007 as a narrow ad-hoc milestone while M006 PR #80 is paused | The operator journey exposed cross-cutting CLI and live-contract failures that do not fit M006’s accepted decision-surface review question |
| 2026-07-29 | Keep proposal and implementation in separate PRs while using one conversation and reviewer | Conversation continuity is useful, but proposal acceptance must remain an explicit merge receipt before implementation starts |
| 2026-07-29 | Bound the first journey to local Chase and observation-only perception | This closes the reproduced usability failure without importing Pi deployment, remote hosting, decision work, or movement authority |
| 2026-07-29 | Treat evaluator control reference as optional for sensor-only perception and mandatory only for reference-dependent evidence | Camera/frame identity and evaluator scoring are different trust boundaries and should not make each other falsely unavailable |
| 2026-07-30 | Use reviewer-driven usability loops without freezing exact presentation | Behavioral, recovery, and safety invariants remain reviewable while visual and usage feedback can repair the active journey in its current PR; later revisions to accepted surfaces use a new frontier instead of rewriting history |
| 2026-07-30 | Audit help and documentation through every level of the bounded journey | Operators should be able to descend from root help to a runnable leaf command without encountering stale terminology, missing flags, or examples that disagree with emitted recovery |
| 2026-07-30 | Make adjacent journey commands consume shared readiness gates | A command must verify the postcondition it names and use the same layer checks and compatible deadline as the next command's preflight so a stable sequence cannot contradict itself |
| 2026-07-30 | Make passive attachment the primary simulator journey | Observation must latch onto the currently exposed vehicle without scenario selection, playback changes, control takeover, or input injection; missing simulator support is an explicit external change request, never a hidden workaround |
| 2026-08-02 | Extend M007 from one accepted journey into measured CLI product-surface stewardship before adding more features | Per-command and realistic-journey reachability can expose import tax, unused capability, missing CLI exposure, and accidental growth before another feature layer compounds it |
| 2026-08-02 | Separate coverage instrumentation, full leaf/usage audit, and capability disposition into sequential frontiers | Each has a different acceptance owner and review question; instrumentation must be trustworthy before its results drive inventory or expose/retain/remove decisions |
| 2026-08-02 | Keep coverage informational and require capability-level ownership before removal or exposure | Executed code is not necessarily correct, unexecuted code is not necessarily dead, and aggregate percentages cannot substitute for behavior, safety, dynamic-path, or product judgments |

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- a completed-milestone ledger entry;
- a durable operator guide for the supported simulator-to-perception journey;
- tracked live acceptance evidence against the current Metrics UI contract;
- a reproducible named-command and realistic-journey owned-source coverage
  report that separates CLI bootstrap/import execution from behavior;
- a complete CLI-leaf and realistic-usage-pattern inventory;
- an owned capability disposition record for code outside declared CLI
  journeys, with any exposure or removal work linked as separate review units;
- a residual-risk statement covering external contract drift, PiRacer parity,
  browser launch, remote views, movement authority, dynamic/platform-only paths,
  and the limits of coverage as a code-value signal.
