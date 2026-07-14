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
bounded plugin lifecycle, replay and comparison reports, isolated classical
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
