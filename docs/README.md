# Documentation Guide

This directory separates current reference material, future-facing synthesis,
and milestone history.
Read this file first when starting work.

## Active Work

Milestone 003, [Test Architecture and Operator Contracts](milestones/003-test-architecture-and-operator-contracts/plan.html),
is active. It reorganizes tests by ownership, adds direct coverage for settled
autonomy contracts, formalizes human and machine CLI output expectations, and
establishes deterministic CI with explicit live-system boundaries.

## Reading Order

1. Read the active milestone plan when one is listed above. If none is active,
   formalize the next goal before implementation.
2. Read [`milestones/completed.md`](milestones/completed.md) for durable context
   from closed work.
3. Read only the relevant documents under `reference/` for current system
   behavior.
4. Consult `synthesis/` when evaluating future work or looking for previously
   researched approaches. Synthesis notes are evidence, not backlog commitments.

Do not treat closed milestone plans as current architecture. They are frozen
records that explain why prior work was shaped the way it was.

## Structure

- `reference/` is the living source of truth for architecture diagrams and
  contracts.
- `synthesis/` relates external research and repository evidence to bounded
  candidate work without making it part of the active milestone.
- `milestones/<number>-<slug>/plan.html` is the detailed plan and status record
  for one milestone.
- `milestones/<number>-<slug>/closeout.md` is the compressed durable summary
  created when that milestone closes.
- `milestones/completed.md` is the append-only high-level ledger of closed
  milestones.

## Milestone Lifecycle

1. Create one numbered milestone directory and one `plan.html` with fixed exit
   criteria, a concrete first PR, and a preparation horizon for later work.
2. Define only the next PR's review question, review shape, dependencies,
   non-goals, and validation in detail before implementation begins.
3. Merge one deliverable at a time by default, record what was learned, and
   then promote the next horizon item into a concrete PR.
4. At closeout, freeze the plan and write a concise `closeout.md` covering the
   outcome, decisions, validation, and remaining debt.
5. Append a short entry to `milestones/completed.md` that links to the frozen
   plan and closeout.
6. Create the next milestone plan and point this file at it.

## Pull Request Review Shapes

- **Deep and narrow:** one new policy, abstraction, or behavioral contract in
  a small set of owning files. Reviewers should have one primary question to
  reason about deeply.
- **Broad and mechanical:** an already-reviewed pattern applied across many
  files. These PRs may be large by file count, but must not introduce new
  behavior or another abstraction.

Do not define a pattern and roll it out broadly in the same PR. PR size is a
logical-complexity budget rather than a line-count target. Every PR should leave
the repository in a complete state and should identify deep-review files,
mechanical files, explicit non-goals, and validation in its description.

Milestones do not require a fixed PR schedule. Keep the end state stable, the
next PR concrete, and later work provisional enough to respond to evidence from
the preceding merge.

Keep the closeout concise. It should preserve decisions and unresolved work,
not duplicate implementation details that belong in reference documents or
source code.
