# Agent Operating Surface

**When to load:** At the start or resumption of every planning, implementation,
review, repair, or closeout session.

**Authority:** This is a derived router for the canonical
[Milestone Planning And Delivery Contract](../milestones/README.md). The
contract wins if any wording conflicts.

## Start

1. Read [docs/README.md](../README.md) for repository documentation navigation.
2. Identify the active milestone plan and run its documented workflow status
   command when milestone work is involved.
3. Load only the task guidance selected below.
4. Read current task data: the active plan, accepted proposal, relevant diff,
   findings, and latest validation evidence.
5. Load the full contract only when this surface directs it, workflow meaning
   is ambiguous, or the workflow itself is being changed.

## Selective Loading

| Current work | Additional guidance |
| --- | --- |
| Scope or author a proposal | [proposal-vs-implementation.md](proposal-vs-implementation.md), [review-unit.md](review-unit.md) |
| Implement an accepted proposal | [proposal-vs-implementation.md](proposal-vs-implementation.md), [validation.md](validation.md) |
| Review a proposal or implementation | [review-unit.md](review-unit.md), [adversarial-matrix.md](adversarial-matrix.md) |
| Repair or re-review findings | [repair-cycle.md](repair-cycle.md), [validation.md](validation.md), and relevant adversarial rows |
| Prepare a handoff | [proposal-vs-implementation.md](proposal-vs-implementation.md) |
| Change process or milestone mechanics | Full canonical contract |

Do not preload every guidance file.

## Conversation State

Use long-running conversations for immediate continuity only. Preserve a short
checkpoint containing:

- repository, branch, PR, base, and head;
- workflow state and current review question;
- unresolved findings or decisions;
- latest validation evidence;
- next permitted action.

Reload durable rules from this directory rather than relying on accumulated
chat history. Reload current milestone state from its plan rather than copying
it into guidance.
