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
