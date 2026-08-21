# Review Unit

**When to load:** When scoping a PR-sized unit, deciding whether work should
split, or performing a proposal, implementation, evidence, or closeout review.

**Authority:** This summarizes
[Work-Unit Model](../milestones/README.md#work-unit-model) and
[Pull Request Delivery](../milestones/README.md#pull-request-delivery) in the
canonical contract. The contract wins if any wording conflicts.

## Scope

- One review unit answers one independently acceptable primary question.
- Size is measured by logical complexity and human attention, not line count.
- Split independently acceptable guarantees, unrelated enforcement owners, or
  substantial live evidence from deterministic implementation.
- Keep coordinated files together when they close one contract at one owning
  boundary.

## Readiness

Select a frontier as current only when the operator can name the want and one
reject condition. Remaining-path edits and current selection belong on the
proposal via the work-order artifact, not a plan-revision PR. Completing a unit
returns to idle; do not implement a successor merely because it was queued.
An empty remaining path is honest at milestone start and after a unit when
nothing further is contracted. Do not delete a contracted node to make room.

Before review, confirm that the PR question is stable, the description matches
the current diff, and validation is exact. Before re-review, confirm that the
repair-cycle ledger names the consolidated verdict and repair revision.

## Review

1. Test the stated contract and owner before reading the implementation as an
   explanation of itself.
2. Report findings first, ordered by severity, with a concrete reproduction and
   required outcome.
3. After a proposal is accepted, raise P0–P2 only for accepted-matrix cases or
   a false review question. Leftover two-shapes, requests to collapse
   internals, and requests to add or polish derived evidence HTML are P3
   unless the operator required that page or the accepted question named one
   type. New failure classes are amendment or residual.
4. After repairs, verify prior findings and then review the current PR against
   that same closed contract.
5. Give one consolidated verdict. Approval accepts this review question, not
   the milestone or unrelated future work.

Use the canonical
[review finding format](../milestones/README.md#review-finding-format).
