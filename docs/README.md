# Documentation Guide

This directory separates current reference material, future-facing synthesis,
and milestone history. Read this file first when starting work.

## Active Milestone

**Milestone 006 — Decision-Facing Perception Readiness**

| Field | Value |
| --- | --- |
| Status | Active |
| Plan | [plan.html](milestones/006-decision-facing-perception-readiness/plan.html) · [plan.md](milestones/006-decision-facing-perception-readiness/plan.md) |
| Cumulative PR | [#70](https://github.com/GeorgeLuo/auto-driving/pull/70) (draft) |
| Current frontier | Decision-facing fitness measures |

Detailed exit criteria, workflow state, and risks live only in the milestone
plan. The current frontier is ready for an independent proposal; perception
implementation must not begin before that proposal is accepted.

## Immediate Pre-Plan (Not Active)

**None.** M006 closeout will decide whether a shadow/non-idle decision pre-plan
is justified.

## Recently Closed

**Milestone 005 — Evidence Memory Foundation** is closed.
Closeout: [005 closeout](milestones/005-evidence-memory-foundation/closeout.md).

**Milestone 004 — Physical Perception Parity** is closed.
Closeout: [004 closeout](milestones/004-physical-perception-parity/closeout.md).

## Reading Order

1. Shared [planning and delivery contract](milestones/README.md)
   ([rendered](milestones/planning-contract.html)).
2. Active milestone plan when one is listed above.
3. [completed.md](milestones/completed.md) for durable closed-work context.
4. Relevant documents under `reference/` for current system behavior.
5. `synthesis/` for research evidence—not backlog commitments.

Do not treat closed milestone plans as current architecture.

## Structure

| Path | Role |
| --- | --- |
| `reference/` | Living architecture and contracts |
| `synthesis/` | Research evidence without commitment |
| `milestones/README.md` | Canonical planning and PR delivery contract |
| `milestones/planning-contract.html` | Generated rendering of the contract |
| `milestones/<n>-<slug>/plan.md` | Canonical active-milestone plan |
| `milestones/<n>-<slug>/plan.html` | Generated plan rendering (do not edit by hand) |
| `milestones/<n>-<slug>/closeout.md` | Durable summary at closeout |
| `milestones/completed.md` | Append-only closed-milestone ledger |

Historical closed milestones may retain hand-authored `plan.html` files without
a `plan.md`. Active milestones use Markdown as the source of truth.
