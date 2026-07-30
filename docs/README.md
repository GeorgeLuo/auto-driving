# Documentation Guide

This directory separates current reference material, future-facing synthesis,
agent operating guidance, and milestone history. Use the selective reading
order below instead of loading the full process contract for every task.

## Active Milestone

**Milestone 007 — CLI Operator Usability**

| Field | Value |
| --- | --- |
| Status | Active (ad-hoc usability milestone) |
| Plan | [plan.html](milestones/007-cli-operator-usability/plan.html) · [plan.md](milestones/007-cli-operator-usability/plan.md) |
| Cumulative PR | [#81](https://github.com/GeorgeLuo/auto-driving/pull/81) (draft) |
| Current frontier | Simulator-to-perception CLI journey |

Detailed exit criteria, workflow state, and risks live only in the milestone
plan. This milestone is a narrow operator-usability chain; it does not absorb
or re-scope Milestone 006 or its paused implementation PR.

## Immediate Pre-Plan (Not Active)

**None.**

## Recently Closed

**Milestone 005 — Evidence Memory Foundation** is closed.
Closeout: [005 closeout](milestones/005-evidence-memory-foundation/closeout.md).

**Milestone 004 — Physical Perception Parity** is closed.
Closeout: [004 closeout](milestones/004-physical-perception-parity/closeout.md).

## Reading Order

1. Short default [agent surface](guidance/agent-surface.md).
2. Active milestone plan when one is listed above.
3. Only the role- or task-specific files selected by the agent surface.
4. Full [planning and delivery contract](milestones/README.md)
   ([rendered](milestones/planning-contract.html)) when resolving ambiguity,
   changing workflow, or directed there by a guidance file.
5. [completed.md](milestones/completed.md) for durable closed-work context.
6. Relevant documents under `reference/` for current system behavior.
7. `synthesis/` for research evidence, not backlog commitments.

Do not treat closed milestone plans as current architecture.
The active milestone plan, not this navigation page, owns current workflow and
frontier state.

## Structure

| Path | Role |
| --- | --- |
| `guidance/` | Short, derived agent operating surface and role guidance |
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
