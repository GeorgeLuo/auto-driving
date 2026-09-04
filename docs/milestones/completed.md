# Completed Milestones

This is an append-only ledger of durable context. Each entry links to the full
frozen plan and its compressed closeout.

## 001 - Automation Engine Foundation

Closed: 2026-07-11

Established the initial vehicle-agnostic automation foundation: one staged
cycle, an explicit idle engine, shared Chase and DonkeyCar cycle hosting,
versioned physical deployment, and a CLI-owned operator workflow. The runtime
does not move autonomously by default; perception is active only in the Chase
controller, while the Pi currently loads the idle decision activation.

Durable decisions: keep stage interfaces no-op friendly, keep simulator map
state outside the vehicle contract, keep concrete behavior in implementations,
and preserve physical deployment as an explicit hashed release flow.

Remaining work: define real decision memory from observed runtime data before
adding non-idle decision behavior.

Full record: [plan](001-automation-engine-foundation/plan.html) and
[closeout](001-automation-engine-foundation/closeout.md).

## 002 - Perception Hardening

Closed: 2026-07-13

Established one component-driven perception contract and CLI experiment flow across
live Chase, live PiRacer, and recorded image sequences. The milestone added
bounded plugin lifecycle, offline application and comparison reports, isolated classical
and FastSAM candidates, temporal scene tracks, and a deployed lightweight
onboard path without granting movement authority. A loopback-only live view
publishes the exact current frame and matching perception record for operator
inspection without turning on artifact recording.

Durable decisions: keep the stage agnostic and algorithms in implementations;
have plugins declare named inputs while generic orchestration injects shared
components and owns lifecycle mechanics; keep plugin output limited to
structured evidence and measurements; treat perception as evidence rather than
world truth; keep temporal state bounded; make diagnostic writes opt-in; use
floor boundaries as the production lightweight path; and retain heavyweight
segmentation and motion tracking as local diagnostics until their value
justifies their cost.

Remaining work: define decision memory from this evidence, add controlled
quality truth, decouple or optimize the roughly 293 ms onboard perception
cadence, and make the Donkey runtime reliably available after power cycles.

Full record: [plan](002-perception-hardening/plan.html) and
[closeout](002-perception-hardening/closeout.md).

## 003 - Test Architecture and Operator Contracts

Closed: 2026-07-15

Established one canonical, ownership-aligned test tree and runner; direct
contracts for stable autonomy behavior; semantic human/JSON CLI checks;
deterministic pull-request CI; informational owned-code coverage; and bounded,
opt-in Chase and Pi validation. The final suite discovers 145 tests, passes 143
by default with two named live skips, and reports a 63.1% owned-code coverage
baseline.

Durable decisions: keep default validation offline and deterministic; place
unit, implementation, integration, lab, and live evidence under explicit
owners; keep shared test support mechanical; reject non-finite control values;
preserve known-good idle behavior across runtime failures; derive operator and
machine output from one semantic result; refresh named built-in activations from
the current catalog; and require manual mode for the first non-moving Pi check.

Remaining work: define bounded decision memory from per-frame observations,
evaluate semantic quality only against explicit task truth, and retain live
simulator, hardware, and motion checks as separately bounded operations.

Full record: [plan](003-test-architecture-and-operator-contracts/plan.html) and
[closeout](003-test-architecture-and-operator-contracts/closeout.md).

## 004 - Physical Perception Parity

Closed: 2026-07-18

Proved that the PiRacer runs always-on perception while Donkey drive mode remains
manual `user`, publishes one exact latest frame/result snapshot over read-only
HTTP, and exposes that path through Automa stream, local overlay, guided
placement check, offline strategy qualification, and a 60-second viability
measurement. The packaged `lightweight_observer` remains the operational
control; lab `floor_continuity` was rejected with `reject_keep_control`.

Durable decisions: separate observation from movement authority with cadence
gating and newest-frame skips; keep status providers free of manager re-entry;
publish only the latest in-memory snapshot; keep physical operator presentation
in Automa; treat documented candidate rejection as a valid close; gate viability
on ≥90% of configured cadence, p95 age ≤1 s, zero control, and user mode.

Remaining work: define bounded decision memory over the proven observation path
(milestone 005), visibly trace retained physical evidence to its exact source
frame, and treat residual right-side miss and clear-floor false positives as
later candidate work rather than blockers for memory.

Full record: [plan](004-physical-perception-parity/plan.html) and
[closeout](004-physical-perception-parity/closeout.md).

## 005 - Evidence Memory Foundation

Closed: 2026-07-28

Delivered bounded decision-cycle memory: typed snapshots and activation, dual
host wiring, operator stage/inspect/stream/reset/replay with opt-in provenance
recording, packaged `BoundedEvidenceLedger`, deterministic recurrence and
same-slot structural conflict policy, and observe-only Pi/Chase lifecycle proofs
with visual provenance. Action remained idle for the entire milestone.

Durable decisions: treat memory as attributed evidence rather than a world
model; keep action idle; bound capacity/age and keep process-local defaults;
require dual-host parity without privileged map inputs; require visual
provenance; keep Chase built-in state evaluator-only; enforce fail-closed
structural conflict without semantic fusion; separate proposal from
implementation review.

Remaining work: activate milestone 006 (decision-facing perception readiness)
through its separate cross-milestone activation step; residual physical
perception quality for movement, process-local memory, and Metrics UI capture
dependency remain documented limits rather than 005 blockers.

Full record: [plan](005-evidence-memory-foundation/plan.md) ·
[plan.html](005-evidence-memory-foundation/plan.html) and
[closeout](005-evidence-memory-foundation/closeout.md).

## 007 - CLI Operator Usability

Closeout packet prepared: 2026-08-24

Whole-milestone acceptance: pending cumulative PR
[#81](https://github.com/GeorgeLuo/auto-driving/pull/81).

Delivered one discoverable, observation-only Chase simulator-to-perception CLI
journey with explicit layer states and recovery, accepted live and realistic
scenario evidence, reproducible named-context coverage, complete public-leaf and
US-01 through US-10 accounting, and owned dispositions for capabilities outside
the declared journeys. No M007 path grants non-idle movement authority.

Durable decisions: attach passively without hidden simulator reconfiguration;
separate simulator, vehicle, deployment, worker, view, and evaluator-reference
state; keep machine-first/HITL evidence bounded and cleanup-owned; treat coverage
as informational; keep all deferred sequences and live findings owned; and
require separately reviewed work before exposing or removing a capability.

Remaining work: open issues #89–#91, five owned `M007-LIVE-*` residuals, seven
deferred and one blocked US sequence, unsupported PiRacer/remote/non-idle claims,
and the `cli-operator-surfaces` expose candidate remain explicit follow-on work.
After successful whole-milestone acceptance, operator focus returns separately
to the active M006 cross-environment shadow-evidence frontier.

Full record: [plan](007-cli-operator-usability/plan.md) ·
[plan.html](007-cli-operator-usability/plan.html) and
[closeout](007-cli-operator-usability/closeout.md).

This is the retained Phase A closeout packet inside cumulative PR #81. It does
not claim that #81 has merged into `main` or that tag `milestone-007` exists.
Phase B must first apply the reviewed terminal plan handoff, and Phase C must
then accept #81 as a whole.

## 007 CLI Operator Usability — cumulative review withdrawn

Cumulative PR [#81](https://github.com/GeorgeLuo/auto-driving/pull/81) was not
merged into `main`. Its Phase C review at cumulative head
`ee2e3056f77bee9a4511877829eb9c46b52d0aa2` recorded a substantial
`changes_requested` verdict because product-boundary findings require new
owned review units.

The preceding 007 entry is retained as the Phase A closeout packet; it is not
mainline milestone closure. M007-06 remains `Unmet` while the milestone returns
to active planning.

## 007 CLI Operator Usability — cumulative review requalified

After the append-only withdrawal, accepted repairs
[#146](https://github.com/GeorgeLuo/auto-driving/pull/146),
[#154](https://github.com/GeorgeLuo/auto-driving/pull/154), and
[#155](https://github.com/GeorgeLuo/auto-driving/pull/155) closed the three
Phase C product findings. The retained Phase A packet in
[closeout.md](007-cli-operator-usability/closeout.md) was updated in place to
cite the rejected cumulative head
`ee2e3056f77bee9a4511877829eb9c46b52d0aa2`, restore head
`9f758d9927d8b870b1d3d2219441fd7410d64b47`, and those repair receipts.

Whole-milestone acceptance remains pending cumulative PR
[#81](https://github.com/GeorgeLuo/auto-driving/pull/81). This section does not
claim a `main` merge or tag `milestone-007`.

## 008 - Perception-Memory Workbench Feasibility

Closeout packet prepared: 2026-09-04

Whole-milestone acceptance: pending cumulative PR
[#167](https://github.com/GeorgeLuo/auto-driving/pull/167).

Delivered one bounded `workbench.image_replay.v1` journey: a long-lived local
workbench replays an ordered image directory through the shared server-side
perception, Observation, and bounded-memory pipeline. PR #174 established the
contract and PR #191 recorded the affirmative Chrome POC acceptance, including
meaningful perception overlays, memory inspection, plugin selection, realtime
pacing, repeated runs, failure/recovery, and observation-only cleanup.

Durable decisions: keep the assessment as the single authority; keep CLI and
page behavior on one server-owned runner; make plugin directories declarative
and selection explicit, including empty raw capture; retain fixed, fastest, and
realtime replay pacing; and keep movement, video/live ingestion, arbitrary or
isolated plugins, remote hosting, recording, and M006 outside this slice.

Remaining work: Phase B must apply the reviewed terminal handoff, then Phase C
must review cumulative PR #167 as a whole before any merge to `main` or
milestone tag. Page `run_id` display and further visual refinement remain
bounded enhancement candidates; source, browser, transport, history/export,
and external Metrics UI limits remain documented residuals.

Full record: [plan](008-cli-decision-workbench/plan.md) ·
[plan.html](008-cli-decision-workbench/plan.html) ·
[assessment](008-cli-decision-workbench/assessment/perception-memory-workbench.md) ·
[closeout](008-cli-decision-workbench/closeout.md).

This is the retained Phase A closeout packet inside cumulative PR #167. It does
not claim that #167 has merged into `main` or that tag `milestone-008` exists.
Phase B must first apply the reviewed terminal plan handoff, and Phase C must
then accept #167 as a whole.
