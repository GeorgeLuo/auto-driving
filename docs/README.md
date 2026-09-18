# Documentation Guide

This directory separates current reference material, future-facing synthesis,
and agent operating guidance.

## Reading Order

1. Short default [agent surface](guidance/agent-surface.md).
2. When the operator explicitly names one, load that exact versioned
   [orchestration policy](guidance/orchestration/README.md).
3. [qca/](../qca/README.md) for optional change inspection. It is not a merge
   gate.
4. Relevant documents under `reference/` for current system behavior.
5. `synthesis/` for research evidence, not backlog commitments.

## Structure

| Path | Role |
| --- | --- |
| `guidance/` | Default agent operating surface and role guidance |
| `guidance/orchestration/` | Operator-selected, versioned orchestration policies |
| `reference/` | Living architecture and contracts |
| `synthesis/` | Research evidence without commitment |
| `deprecated/` | Historical delivery records. Do not use. |

## Deprecated

Do not use [deprecated/](deprecated/). Its first file is the warning.
