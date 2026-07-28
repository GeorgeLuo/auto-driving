# Documentation Guide

This directory separates current reference material, future-facing synthesis,
and milestone history. Read this file first when starting work.

## Active Milestone

**Milestone 005 — Evidence Memory Foundation** (closeout in progress)

| Field | Value |
| --- | --- |
| Status | Active until post-merge closeout handoff |
| Plan | [plan.html](milestones/005-evidence-memory-foundation/plan.html) · [plan.md](milestones/005-evidence-memory-foundation/plan.md) |
| Closeout judgment | [closeout.md](milestones/005-evidence-memory-foundation/closeout.md) |
| Cumulative PR | [#68](https://github.com/GeorgeLuo/auto-driving/pull/68) (draft; mark ready after handoff) |
| Current frontier | Milestone closeout — see plan Current Delivery |

Detailed exit criteria, frontiers, and risks live only in the milestone plan.
Do not treat this guide as a second progress log. After Phase B handoff, move
005 to Recently Closed and leave 006 as the immediate pre-plan until its own
activation step.

## Immediate Pre-Plan (Not Active)

**Milestone 006 — Decision-Facing Perception Readiness**

- Plan: [plan.html](milestones/006-decision-facing-perception-readiness/plan.html)
- Status: pre-plan, queued after 005 closeout (activate decision recorded in
  005 closeout; not Active product work)
- Do not implement 006 while 005 plan Status is still Active unless the 005
  decision log records an explicit parallel exception

## Recently Closed

**Milestone 004 — Physical Perception Parity** is closed.
Closeout: [004 closeout](milestones/004-physical-perception-parity/closeout.md).

**Milestone 005** judgment is published in
[closeout.md](milestones/005-evidence-memory-foundation/closeout.md); plan
Status becomes closed only after the closeout implementation handoff.

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
