# Documentation Guide

This directory separates current reference material, future-facing synthesis,
agent operating guidance, and historical delivery records.

## Reading Order

1. Short default [agent surface](guidance/agent-surface.md).
2. When the operator explicitly names one, load that exact versioned
   [orchestration policy](guidance/orchestration/README.md).
3. [qca/](../qca/README.md) for optional change inspection. It is not a merge
   gate.
4. Relevant documents under `reference/` for current system behavior.
5. `synthesis/` for research evidence, not backlog commitments.
6. [completed.md](milestones/completed.md) and historical `plan.md` files only
   when looking up closed work.

Do not treat closed plans as current architecture.

## Structure

| Path | Role |
| --- | --- |
| `guidance/` | Default agent operating surface and role guidance |
| `guidance/orchestration/` | Operator-selected, versioned orchestration policies |
| `reference/` | Living architecture and contracts |
| `synthesis/` | Research evidence without commitment |
| `milestones/` | Parked historical delivery records, including `plan.md` files |

## Historical delivery records

Closed plans, the old delivery contract, and `workflow.py` remain under
[milestones/](milestones/). They are parked. Do not load them for new work
unless restoring that process.

```sh
python3 docs/milestones/workflow.py status \
  --plan docs/milestones/<number>-<slug>/plan.md
```

Contract: [milestones/README.md](milestones/README.md) ·
[planning-contract.html](milestones/planning-contract.html).
Closed ledger: [completed.md](milestones/completed.md).
