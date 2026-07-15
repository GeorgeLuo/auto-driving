# Documentation Guide

This directory separates current reference material, future-facing synthesis,
and milestone history.
Read this file first when starting work.

## Active Work

Milestone 004, [Evidence Memory Foundation](milestones/004-evidence-memory-foundation/plan.html),
is active. It defines bounded, inspectable memory from decision observations,
wires the same lifecycle through simulator and physical hosts, and keeps action
idle while memory semantics and operator workflows are validated.

## Reading Order

1. Read the shared [`milestones/README.md`](milestones/README.md) planning and
   pull-request delivery contract, or use its
   [rendered view](milestones/planning-contract.html).
2. Read the active milestone plan when one is listed above. If none is active,
   formalize the next goal before implementation.
3. Read [`milestones/completed.md`](milestones/completed.md) for durable context
   from closed work.
4. Read only the relevant documents under `reference/` for current system
   behavior.
5. Consult `synthesis/` when evaluating future work or looking for previously
   researched approaches. Synthesis notes are evidence, not backlog commitments.

Do not treat closed milestone plans as current architecture. They are frozen
records that explain why prior work was shaped the way it was.

## Structure

- `reference/` is the living source of truth for architecture diagrams and
  contracts.
- `synthesis/` relates external research and repository evidence to bounded
  candidate work without making it part of the active milestone.
- `milestones/README.md` is the shared milestone format, rolling-horizon, and
  pull-request delivery contract.
- `milestones/planning-contract.html` is its generated browser rendering; active
  plans expose it in an expandable section.
- `milestones/<number>-<slug>/plan.html` is the detailed plan and status record
  for one milestone.
- `milestones/<number>-<slug>/closeout.md` is the compressed durable summary
  created when that milestone closes.
- `milestones/completed.md` is the append-only high-level ledger of closed
  milestones.

The shared milestone contract owns lifecycle, plan-format, PR-review, rolling
horizon, and evidence rules. Keep this guide limited to documentation discovery
so those development instructions have one authoritative location.
