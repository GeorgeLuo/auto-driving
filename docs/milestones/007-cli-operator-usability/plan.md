# Milestone 007 — CLI Operator Usability

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/007-cli-operator-usability` |
| Cumulative PR | [#81](https://github.com/GeorgeLuo/auto-driving/pull/81) (draft until whole-milestone closeout) |
| Current frontier | PiRacer perception-inspection compatibility |
| Started | 2026-07-29 |
| Action policy | Observation-only; no applied vehicle movement |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Make the Automa CLI a dependable and inspectable product surface. First, make
one complete Chase operator journey usable from a local simulator URL through
an observation-only, frame-correlated browser view without commandeering the
current simulator session. After that primary journey is live-accepted, declare
and exercise additional safe realistic multi-command sequences on the
repository-owned session runner (machine-first, then human visual confirmation
where required), repair only the product or operator-facing defects those
sequences prove, and refine CLI/view cues needed for judgment. Then measure
which owned source branches named CLI commands and realistic multi-command
journeys actually execute, audit every CLI leaf into an explicit usage pattern,
reconcile every proposed #88 sequence into a committed definition and
disposition, and classify production capabilities those journeys do not reach
so later work can expose, retain, or remove them through evidence rather than
accumulation.
Failures must identify the owning boundary without requiring knowledge of
internal WebSocket paths, runtime files, or process topology; coverage remains
an investigative signal rather than a claim that executed code is correct or
unexecuted code is dead.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Primary demonstration | Metrics UI intended at `http://localhost:5050` already exposes a Chase-compatible vehicle and front camera in any scenario/control/playback state; no pre-existing Automa bundle or worker is required | Run the exact command sequence below to passively inspect the current vehicle, stage packaged perception, start observation-only automation, open and inspect its perception view, then stop the worker | The browser shows a current camera frame and frame-matched observation/perception output; CLI output distinguishes simulator, vehicle, deployment, worker, and view state; scenario, playback, control source, and input remain unchanged; cleanup leaves no worker running | M007-01, M007-02, M007-03, M007-04 |
| Inspect current operator state | Any combination of online/offline simulator, connected/disconnected frontend, deployed/undeployed bundle, running/stopped worker, and available/unavailable view | Run the documented status/discovery commands in human or `--json` form | Every layer has one unambiguous state, ownership boundary, and next action; “active” never implies a running automation worker | M007-01, M007-04 |
| Recover a failed startup | Chase is reachable but frontend, passive capture capability, capture contract, automation process, or perception view is not ready | Follow the exact recovery emitted by the failing CLI surface, then retry; simulator reconfiguration remains an explicit opt-in rather than a hidden recovery | Recovery reaches the next state or names the missing external simulator capability and minimal requested change without a generic timeout, silent scenario selection, or collapsed “invalid identity or control reference” message | M007-02, M007-03, M007-04 |
| Validate the live journey | Current local Metrics UI and simulator deployment | Run the bounded live CLI acceptance procedure | One processed frame, a healthy loopback view, observation-only authority, preserved scenario/playback/control/input state, and the expected human/JSON state are recorded without default history writes | M007-05 |
| Exercise realistic multi-command scenarios | Primary six-step journey accepted; live CLI session runner available; Metrics UI Chase session ready for observation-only work | On the #88 candidate catalog families (not a thin help/status-only set), declare runner sequences with operator question, real commands, prerequisites, safety/cleanup, and one primary human-scannable confirmation; run machine-first; elevate only machine-green sequences that require visual judgment to HITL; dispose findings and apply only bounded product/CLI-output repairs | Representative families exercised or explicitly blocked with owner; no sequence uses raw JSON/record path as sole human success signal; machine-first/HITL artifacts and repair disposition committed | M007-10 |
| Trace source use by CLI journey | Clean repository state with the existing branch-aware owned-Python coverage configuration | Record named CLI commands and multi-command journeys with stable contexts, combine foreground and Python subprocess/background-worker data, and compare the result with a measured CLI bootstrap/import baseline | A reproducible report attributes executed statements and branches to command/journey contexts, separates shared import/bootstrap cost from command-specific behavior, preserves source revision and command identity, and imposes no aggregate percentage gate | M007-07 |
| Audit the complete CLI surface | Parser/help surface and journey-coverage collector accepted | Enumerate every public CLI leaf and map it to realistic operator/developer usage patterns, prerequisites, side effects, safety class, expected output, owning boundary, and deterministic or live validation; reconcile every #88 US-01 through US-10 candidate into the same committed sequence registry | Every leaf is assigned to at least one named realistic pattern or explicitly classified unsupported/deprecated; every proposed sequence has a stable definition and explicit `passed`, `ready`, `blocked`, or `deferred` disposition, with owner and unlock condition for blocked/deferred work; hazardous or external leaves are documented without being run unsafely | M007-08 |
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
proposal. Realistic multi-command scenarios beyond that primary path are added
only through the scenario-continuity unit and later audit, not by rewriting the
accepted six-step evidence. The later full-surface audit inventories the existing
CLI and its source reachability before proposing redesign, new exposure, or
deletion; it does not retroactively widen the accepted primary journey.

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| Passive attachment to a currently exposed Chase-compatible vehicle, plus explicit simulator preparation as an operator-chosen recovery | Redesigning unrelated perception, decision, memory, Pi, or command-hierarchy behavior before reachability and usage evidence is reviewed |
| Human-readable defaults and complete machine-readable output for the bounded journey | A new interactive TUI, desktop application, or general service manager |
| HTTP/WS endpoint normalization, passive-capability reporting, and actionable frontend recovery without hidden reconfiguration | Supporting arbitrary remote browser authentication, public view hosting, or non-loopback view access |
| Observation-only camera/perception startup that preserves simulator scenario, playback, control source, and input when evaluator-only reference data is unavailable | Weakening camera frame identity, allowing privileged evaluator data into controller inputs, or claiming shadow-evaluation evidence when its reference is absent |
| Exact startup diagnostics, bounded timeout semantics, and current operator documentation | Hiding external contract drift with retries, indefinite waits, or permissive malformed-capture acceptance |
| Deterministic tests plus one opt-in live acceptance unit against the current simulator contract | Making a browser or live simulator mandatory for the default deterministic test suite |
| Named safe realistic CLI sequences on the live session runner for the #88 representative families, machine-first then HITL with one human-scannable confirmation each, and targeted product or operator-facing repairs those sequences force | Thin help/status-only catalogs that avoid perception/memory loops; large new experiment/feature programs (for example same-frame matrices or transactional live trials as required deliverables); Metrics UI product redesign owned outside this repository; hardware/movement/destructive leaves run merely for coverage; or a full leaf inventory before declared scenarios exist |
| Branch-aware, subprocess-complete owned-Python coverage attributed to named CLI command and journey contexts | Treating import-time execution as feature use, measuring Metrics UI JavaScript in the Python report, or setting an arbitrary repository-wide coverage gate |
| Complete CLI-leaf inventory and realistic usage-pattern catalog, including prerequisites, side-effect/safety classification, and committed reconciliation of all #88 US-01 through US-10 candidates | Executing hardware-, movement-, destructive-, or external-state commands merely to increase coverage |
| Capability-level review of owned source not reached by declared CLI journeys | Automatically deleting uncovered code, exposing every internal primitive, or pre-authorizing feature/removal changes before separate review |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M007-01 | Automa exposes and documents one consistent operator state model that distinguishes simulator availability, simulator frontend readiness, vehicle discoverability, local automation deployment, worker liveness, and perception-view availability in concise human output and complete `--json` output | Met | Consistent simulator, vehicle, deployment, worker, view, and evaluator-reference state vocabulary plus shared next-step readiness gates in human and JSON CLI surfaces in PR #84 |
| M007-02 | An operator starting from `http://localhost:5050` can prepare the supported Chase scenario and discover `chase-sim-chaser` without manually deriving `/ws/control`; disconnected, stale, wrong-game, and unavailable-camera states name the failed boundary and exact recovery | Met | Local HTTP URL normalization, passive Chase discovery, explicit-only configured preparation, and exact frontend/game/camera/capability recovery in PR #84 |
| M007-03 | Observation-only automation can publish a current camera/perception browser view when sensor image and frame identity are valid even if evaluator-only control reference data is unavailable; workflows that require that reference fail closed with an explicit missing-reference status | Met | Passive observation-only sensor/perception startup preserves scenario/playback/control/input state, separates optional evaluator reference, and keeps reference-dependent operations fail-closed in PR #84 |
| M007-04 | Startup, status, and view commands use bounded operation-level timeout semantics, preserve stable human/JSON error categories, and provide current help/README examples for the complete journey and its recovery paths | Met | PR #146 closes the Phase C timeout input-envelope finding at the shared CLI boundary: every affected primary consumer rejects non-positive and non-finite timeout values before discovery, staging, or worker/view work, with stable exit-2 errors and no traceback, while valid bounded timeout behavior is preserved. |
| M007-05 | A tracked live acceptance unit against the current local Metrics UI contract proves one observation-only processed frame, healthy loopback view, exact layer states, no applied movement, and no default recording; contract drift fails rather than skipping | Met | Tracked live acceptance in PR #88 proves one current correlated camera/perception frame, healthy loopback rendering, truthful layer states, observation-only no-applied-control authority, protected-state preservation, no default run history, and stopped-worker cleanup against exact recorded auto-driving and Metrics UI commits |
| M007-06 | Closeout confirms the primary demonstration, reconciles durable CLI documentation, records the accepted journey-coverage and full-leaf audit outcomes, verifies every #88 US-01 through US-10 candidate has a committed definition and disposition, and states every retained/unexposed capability and remaining external simulator, PiRacer, remote-view, or non-idle-control limit | Unmet | Closeout only after M007-01 through M007-05 and M007-07 through M007-10; no proposed sequence may remain unlisted or ownerless |
| M007-07 | A reproducible CLI journey-coverage collector attributes owned-Python statement and branch execution to named commands and multi-command journeys across foreground and Python subprocess/background-worker boundaries, separates bootstrap/import footprint from command-specific behavior, records exact source/command identity, and remains informational | Met | PR #107 provides a versioned manifest and collection-bound pass report attributing branch-aware owned-Python execution to the accepted primary and continuity command/journey contexts across foreground and background Python processes, with pre-interpreter environment isolation, canonical dependency identity, bootstrap classification, immutable receipts, exact digest semantics, completeness/freshness checks, cleanup, and no correctness, dead-code, or percentage-gate claim |
| M007-08 | A complete generated-and-reviewed inventory maps every public CLI leaf to realistic usage patterns, prerequisites, side effects, safety class, output contract, owning boundary, and deterministic/live validation status without requiring unsafe execution, and reconciles every #88 US-01 through US-10 proposed sequence into a committed registry with stable identity, current commands and prerequisites, primary human confirmation, cleanup, coverage treatment, and explicit disposition; blocked/deferred entries name an owner and unlock condition | Met | PR #122 delivers complete-to-parser leaf accounting with all M007-08 leaf dimensions, #88-bound US-01..US-10 reconciliation with argv-valid current commands, semantic cite/execute passed rules, required coverage treatment, and visible residuals for deferred work and known LIVE defects |
| M007-09 | Owned production code not reached by the declared CLI journey set is grouped by capability and reconciled with tests, other entrypoints, dynamic/platform paths, and ownership; every group is flagged to expose through CLI, retain with explicit reason, or remove through separately reviewed work | Met | PR #138 groups unreached owned production code from the sealed journey-coverage report, reconciles tests/entrypoints/dynamic-or-platform paths and ownership, and records an owned expose, retain, or remove candidate for every group without using a coverage percentage as authorization |
| M007-10 | Beyond the accepted primary six-step journey, the live CLI session runner declares and executes a representative set of safe realistic multi-command sequences drawn from the #88 candidate catalog families (offline perception feedback; live configuration/plugin swap with restoration; perception→memory lifecycle; and, where prerequisites allow, ablation, temporal backpressure, and deterministic replay)—each with machine-first execution, one primary human-scannable confirmation (concise CLI verdict or launched frontend/generated review surface; never raw JSON or a record path alone), unconditional cleanup/restoration, durable finding disposition, and only the bounded product or operator-facing repairs those sequences prove | Met | PR #100 passes required family aggregates with safety preflight, machine-first/HITL confirmations, US-04 restorable restore verification, offline source lineage, deterministic evidence freshness finalizer, and only bounded-table repairs |

## Current Delivery

### Current Frontier

**PiRacer perception-inspection compatibility**

- Workflow state: ready_for_implementation
- Proposal branch: `m007/piracer-perception-inspection-compatibility-proposal`
- Implementation branch: `m007/piracer-perception-inspection-compatibility`
- Proposal path: `docs/milestones/007-cli-operator-usability/proposals/piracer-perception-inspection-compatibility.md`
- Accepted proposal: [#152](https://github.com/GeorgeLuo/auto-driving/pull/152) at `a30e4db059abfc2032253666d94eb9a6487ead38` (reviewed head `f581de64c01295393235e4311535df227ceb7656` by `GeorgeLuo` as `OWNER` at `2026-08-26T02:16:07Z`)
- Review kind: Review repair
- Review question: Can `vehicles info perception --id piracer` retain successful offline inspection of a locally staged `active.json` while also enriching that result with reachable live PiRacer observation and local-view state, without making a PiRacer network outage invalidate the offline inspection path?
- Acceptance owner: `cli/automa_cli/perception.py`
- Exit criteria affected: `M007-06`
- Prerequisite: Phase C PiRacer perception-inspection finding from PR #81 is reproduced and remains routed as a new owned product review unit from the restored Active/idle milestone.
- Milestone-level non-goal: No PiRacer hardware/deployment redesign, no repair of PR #81, and no repair of the separate Chase image-envelope finding.

### Next-Frontier Candidate

**None**

- Reason: No remaining work-order node is contracted.
- Revisit when: The next proposal may introduce a node.

### Frontier Map

- Path: none
- Cadence: linked-list

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
| Live CLI operator acceptance | ready_for_implementation | Proposal amendment PR #94 accepted at 012a63963a55692279e74eba069edf7a76f35f6e. |
| Live CLI operator acceptance | implementation_in_review | Started m007/live-cli-acceptance. |
| Live CLI operator acceptance | accepted | Implementation PR #88 merged at 3b6ca82dbd5e1a0793b5f534bacb6c84b7cba123. |
| CLI journey coverage foundation | ready_for_proposal | Promoted after implementation PR #88. |
| Realistic CLI scenario continuity | ready_for_proposal | Plan revision: insert post-acceptance continuity for named realistic multi-command sequences on the live session runner (machine-first/HITL, targeted product/UI repairs, M007-10) before coverage instrumentation; requeue CLI journey coverage foundation as the next candidate; freeze representative #88 catalog families and the human-scannable confirmation standard so a thin nonvisual catalog cannot satisfy M007-10. |
| Realistic CLI scenario continuity | proposal_in_review | Started m007/scenario-continuity-proposal. |
| Realistic CLI scenario continuity | ready_for_implementation | Proposal PR #99 accepted at 7fd92683fc424addd1549bed81ef707e5259937d. |
| Realistic CLI scenario continuity | implementation_in_review | Started m007/scenario-continuity. |
| Realistic CLI scenario continuity | accepted | Implementation PR #100 merged at 4b8204bb847d9a6dd067b609e99b87ddc554b139. |
| CLI journey coverage foundation | ready_for_proposal | Promoted after implementation PR #100. |
| CLI journey coverage foundation | ready_for_proposal | Plan revision: make exhaustive #88 US-01 through US-10 definition and disposition an explicit M007-08 and milestone-closeout requirement while preserving accepted M007-10 and the current coverage frontier. |
| CLI journey coverage foundation | ready_for_proposal | Plan revision: queue the complete CLI surface and #88 US-01 through US-10 sequence audit as the reviewed successor required for a successful coverage handoff. |
| CLI journey coverage foundation | proposal_in_review | Started m007/cli-journey-coverage-proposal. |
| CLI journey coverage foundation | ready_for_implementation | Proposal PR #106 accepted at 47a6ac9d5f9060f420477dcddae07dea2da1eafa. |
| CLI journey coverage foundation | implementation_in_review | Started m007/cli-journey-coverage. |
| CLI journey coverage foundation | accepted | Implementation PR #107 merged at ff75a200ffa8df2cd41458893a5c0e2aafef29c7. |
| Complete CLI surface and sequence audit | ready_for_proposal | Promoted after implementation PR #107. |
| Complete CLI surface and sequence audit | proposal_in_review | Started m007/cli-surface-audit-proposal. |
| Complete CLI surface and sequence audit | ready_for_implementation | Proposal PR #113 accepted at 75072a9e9270d8c8e8f866c39a52f6bdce0d56ca (reviewed head `44158825e8b8f9566617702e31a570d169d339b2` by `GeorgeLuo` as `OWNER` at `2026-08-14T07:32:32Z`). |
| Complete CLI surface and sequence audit | implementation_in_review | Started m007/cli-surface-audit. |
| Complete CLI surface and sequence audit | accepted | Implementation PR #122 merged at 5c09fb32dc196b79e90c2a54fde78b896b41cb49. |
| Capability disposition outside CLI journeys | ready_for_proposal | Promoted after implementation PR #122. |
| Capability disposition outside CLI journeys | proposal_in_review | Started m007/capability-disposition-proposal. |
| Capability disposition outside CLI journeys | ready_for_implementation | Proposal PR #133 accepted at 569d59de2faaca59dab5f5fa04d3da366c9d211c (reviewed head `7e30872f32a123f56556167c0c428a9506474dbb` by `GeorgeLuo` as `OWNER` at `2026-08-21T06:37:57Z`). |
| Capability disposition outside CLI journeys | implementation_in_review | Started m007/capability-disposition. |
| Capability disposition outside CLI journeys | accepted | Implementation PR #138 merged at 460e2827bd6b586e75bc698593be064f4c10e6f9. |
| Milestone closeout | proposal_in_review | Started m007/closeout-proposal. |
| Milestone closeout | ready_for_implementation | Proposal PR #143 accepted at 2ab7955b953f1d5863ee032db38271ca50d111a7 (reviewed head `c9b7185df1f2ddba8c74b735e082881dd9501c45` by `GeorgeLuo` as `OWNER` at `2026-08-24T18:18:59Z`). |
| Milestone closeout | implementation_in_review | Started m007/closeout. |
| Milestone closeout | accepted | Implementation PR #144 merged at ab72c7befdca8d69fe12f9d8c6d1ce554aeb5818. |
| Timeout input-envelope consistency | proposal_in_review | Started m007/timeout-input-envelope-proposal. |
| Timeout input-envelope consistency | ready_for_implementation | Proposal PR #145 accepted at 02f0d9fc1cf5b85fde4a118f4f7e87b8464ff01c (reviewed head `b3997bec7e074acaf1cc9b33e6e8f17f887968c7` by `GeorgeLuo` as `OWNER` at `2026-08-25T06:19:37Z`). |
| Timeout input-envelope consistency | proposal_amendment_in_review | Started proposal amendment m007/amend-historical-capability-validation. |
| Timeout input-envelope consistency | ready_for_implementation | Proposal amendment PR #147 accepted at 2027edcf1ecef71938a818145531a1a861724fee (reviewed head `18457f0a6d8172ecc0dc9fa4eddd5d5117b6b0ae` by `GeorgeLuo` as `OWNER` at `2026-08-25T08:09:07Z`). |
| Timeout input-envelope consistency | implementation_in_review | Started m007/timeout-input-envelope. |
| Timeout input-envelope consistency | accepted | Implementation PR #146 merged at f6d221c0c602e648efc4bdd355c909a9bca3fa12. |
| PiRacer perception-inspection compatibility | proposal_in_review | Started m007/piracer-perception-inspection-compatibility-proposal. |
| PiRacer perception-inspection compatibility | ready_for_implementation | Proposal PR #152 accepted at a30e4db059abfc2032253666d94eb9a6487ead38 (reviewed head `f581de64c01295393235e4311535df227ceb7656` by `GeorgeLuo` as `OWNER` at `2026-08-26T02:16:07Z`). |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |
| #84 | Can a Chase operator move from a local Metrics UI URL to a healthy observation-only perception browser view through discoverable Automa commands that distinguish every runtime layer and return exact, bounded recovery when the frontend, capture contract, worker, or view is unavailable? | Accepted | M007-01, M007-02, M007-03, M007-04 | Passive Chase simulator-to-perception CLI journey with aggregate layer status, shared sequential-readiness gates, HTTP/WS normalization, observation-only first-frame view startup, simulator-state preservation, exact capture/reference diagnostics, operation-level deadlines, cross-level help audit, and durable operator documentation in PR #84 |
| #88 | Does the accepted simulator-to-perception CLI journey work end to end against the current local Metrics UI deployment with one processed observation-only frame, a healthy browser view, truthful layer states, no applied movement, and no default recording? | Accepted | M007-05 | User-led live CLI operator acceptance against the recorded current Metrics UI commit, with help and flow audit, human/JSON transcript, correlated browser publication, observation-only authority, unchanged protected simulator state, no default recording, terminal cleanup, and tracked evidence in PR #88 |
| #100 | After the primary six-step journey is live-accepted, can the repository-owned live CLI session runner declare and execute representative safe multi-command sequences beyond that journey—anchored to the #88 candidate catalog families (offline perception feedback / US-03-class; live configuration or plugin swap with restoration / US-04-class; perception→memory lifecycle / US-05 and US-08-class; and, when prerequisites allow, ablation, temporal backpressure, and deterministic replay / US-06, US-07, US-09-class)—with machine-first execution, one primary human-scannable confirmation per sequence, durable findings with owners, and only the bounded product or operator-facing repairs those sequences prove necessary? | Accepted | M007-10 | Realistic CLI scenario continuity in PR #100: required family aggregates continuity.offline_perception, continuity.live_config_swap, and continuity.memory_lifecycle each passed on the live session runner with fail-closed safety preflight; machine-first then conditional HITL with human-scannable confirmations; US-04 restorable snapshot and restore verified; evidence freshness finalizer matched final product/runner/catalog/Metrics UI identities; durable finding disposition; repairs limited to the proposal bounded table; tracked under docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/ |
| #107 | Can a developer record reproducible branch-aware owned-Python coverage for named CLI commands and multi-command journeys across foreground and Python subprocess/background-worker execution while separating bootstrap/import footprint from command-specific behavior and avoiding false correctness or dead-code claims? | Accepted | M007-07 | Reproducible branch-aware owned-Python CLI journey coverage in PR #107: a digest-bound command/context manifest; unpredictable collection-bound shard provenance; pre-interpreter ambient Coverage.py refusal and session-contained effective configuration; isolated foreground, subprocess, and SIGTERM-flushed background-worker collection; complete expected-context and process receipts; canonical requirements/interpreter/all-distribution identity; raw plus bootstrap/shared/command-specific statement and arc attribution; immutable timestamp receipts and an exact byte-stable public-API report digest; terminal cleanup; explicit correctness/dead-code/numeric-gate non-claims; and tracked pass evidence under docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/ |
| #122 | Can one committed CLI usage registry prove complete parser-leaf and #88 US-01 through US-10 accounting, with every public leaf mapped to realistic usage, prerequisites, side effects and safety, expected output, owning boundary, and deterministic or live validation, and every sequence assigned stable commands, confirmation, cleanup, coverage treatment, and an explicit passed, ready, blocked, or deferred disposition with owned unlock conditions, while machine-first and HITL evidence covers safe executable patterns without running hazardous or external entries unsafely? | Accepted | M007-08 | Complete CLI surface and sequence audit in PR #122: argparse-derived leaf inventory with mandatory overlay dimensions; committed #88 catalog authority snapshot and US-01 through US-10 registry bound to it; parser-aware argv validation; semantic hybrid passed citation (rejecting incomplete rehearsals) or new execute evidence; required coverage treatment enums; validators and cross-checks; help-drift report; linked LIVE residuals without product repair; rendered residual rollup; tracked evidence under docs/milestones/007-cli-operator-usability/evidence/cli-surface-audit/ |
| #138 | Can owned production code not reached by the declared CLI journey set be grouped by capability and reconciled with tests, other entrypoints, dynamic or platform paths, and ownership so every group is flagged to expose through CLI, retain with an explicit owner and reason, or remove through separately reviewed work, without authorizing feature or deletion solely by a coverage percentage? | Accepted | M007-09 | Capability disposition outside CLI journeys in PR #138: unreached owned production code derived from sealed M007-07 report; every region in exactly one capability group; tests/entrypoints/platform/owner reconciled; expose/retain/remove candidates with non-percentage reasons; validators reject omission and percentage-as-authorization; derived HTML of that record; tracked evidence under docs/milestones/007-cli-operator-usability/evidence/capability-disposition/ |
| #144 | Is milestone 007 complete as a whole—its supported CLI journeys remain documented, every exit criterion is backed by accepted evidence, every #88 US-01 through US-10 sequence and every unreached capability has an accountable disposition, residual limits are explicit, and the cumulative milestone is ready for whole-milestone review without hiding follow-on product work? | Cumulative review rejected; closeout packet retained; M007-06 remains Unmet | M007-06 | Phase C rejected whole-milestone acceptance on PR #81; Phase B close commit reverted; completed.md gained an append-only withdrawal section; exceptional advance receipt returned current to idle without marking M007-06 Met |
| #146 | Do all affected primary CLI commands reject non-positive and non-finite timeout inputs through one stable input/error boundary before execution, without tracebacks, while preserving valid timeout behavior? | Accepted | `M007-04` | Timeout input-envelope consistency in PR #146: vehicles status, vehicles automation run, and vehicles update perception reject zero, negative, NaN, and infinite --timeout-s values before command work with stable exit-2 human or existing machine-readable errors and no traceback; finite positive and default timeout behavior remains unchanged; focused regressions and the repository suite pass. |

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| Metrics UI may evolve independently of this repository | Unit fixtures can remain green while live atomic-capture payloads drift | Freeze the consumed sensor/reference distinction, add reference-less fixtures, and require a non-skipping opt-in live contract unit |
| A browser tab can be visibly open before its Play WebSocket role is registered | A one-second probe can report a false unavailable state with no useful recovery | Distinguish server/frontend/game/camera states and use one bounded operation deadline with exact recovery |
| Evaluator reference data is useful for scoring but is not sensor input | Requiring it for camera capture prevents legitimate observation-only perception; accepting malformed identity would weaken correlation | Validate sensor identity independently, model evaluator reference as optional/unavailable, and keep reference-required scoring fail-closed |
| Browser opening is platform-dependent | An otherwise healthy worker could be reported failed because the OS cannot launch a browser | Keep view health authoritative; browser launch is explicit and reports its own non-fatal result and URL |
| Running every CLI leaf can be unsafe or environment-dependent | A coverage target could encourage movement, destructive operations, hardware access, or misleading skips | Inventory every leaf, but execute only declared safe patterns; record prerequisites and non-executed live/hazardous classifications explicitly |
| Confirmed exploratory product defects from PR #88 remain deferred without owners | Safe multi-apply and honest readiness cues stay broken while process work advances | Scenario continuity must dispose #89 / M007-LIVE-001..005 (repair, external-issue, or explicit non-blocking deferral with owner) rather than re-parking them under later audit |
| Cited sequence passed status is historical, not continuous HEAD verification | Regressions after #88/#100 may not be visible in the audit registry until a separate re-proof | Optional live smoke or product-unit re-proof when journeys are reopened; residual risk at closeout |
| Capability dispositions are historical to the sealed M007-07 report | Later product commits can change what is unreached without updating the candidate record | Closeout cites the record digest; a later unit recaptures #107 if reachability must be refreshed |

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
| 2026-08-06 | Insert realistic CLI scenario continuity after live acceptance and before coverage instrumentation | PR #88 delivered the primary six-step pass and the live session runner; the meta program still needs named sequences beyond that journey, machine-first/HITL evidence, UI/product repairs those sequences force, and owned disposition of exploratory defects before measuring source reachability |
| 2026-08-06 | Reuse the live CLI session runner as the continuity evidence system | Do not invent a second harness; expand catalogs and procedure on the accepted runner, keep large feature programs (#90/#91) and full leaf audit out of the continuity unit |
| 2026-08-07 | Freeze M007-10 acceptance breadth to the #88 candidate scenario families and confirmation standard | A plan-level “reviewed set” without representative families or human-scannable confirmation could pass with a trivial machine-only help/status catalog; the proposal may choose exact commands and repair order but must not invent a thinner gate |
| 2026-08-11 | Require every #88 US-01 through US-10 proposal to have a committed definition and disposition before M007 closeout | PR #100 correctly accepted a representative scenario set under M007-10; the later complete inventory must prevent optional or deferred candidates from disappearing without reopening that accepted contract or forcing unsafe execution |

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- a completed-milestone ledger entry;
- a durable operator guide for the supported simulator-to-perception journey;
- tracked live acceptance evidence against the current Metrics UI contract;
- declared realistic multi-command scenario catalogs with machine-first and
  HITL evidence beyond the primary six-step journey, plus disposition of the
  product defects those sequences force;
- a reproducible named-command and realistic-journey owned-source coverage
  report that separates CLI bootstrap/import execution from behavior;
- a complete CLI-leaf and realistic-usage-pattern inventory, including a
  committed US-01 through US-10 registry with current sequence definitions,
  confirmation/cleanup/coverage treatment, and owned unlock conditions for
  blocked or deferred entries;
- an owned capability disposition record for code outside declared CLI
  journeys, with any exposure or removal work linked as separate review units;
- a residual-risk statement covering external contract drift, PiRacer parity,
  browser launch, remote views, movement authority, dynamic/platform-only paths,
  and the limits of coverage as a code-value signal.
