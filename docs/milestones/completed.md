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
(milestone 005), including visual provenance of retained physical evidence.
Residual right-side miss and clear-floor false positives are immediate deferred
work captured only in the
[006 pre-plan](006-decision-facing-perception-readiness/plan.html), not an
expanding candidate backlog.

Full record: [plan](004-physical-perception-parity/plan.html) and
[closeout](004-physical-perception-parity/closeout.md).
