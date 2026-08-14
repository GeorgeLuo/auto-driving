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

Before review, confirm that the PR question is stable, the description matches
the current diff, validation is exact, limitations are explicit, and the
adversarial pass is current. Before re-review, also confirm that the repair-cycle
ledger names the consolidated verdict and repair revision. The second
substantial cycle requires a completed human escalation receipt; a third cannot
remain in the same review unit.

## Review

1. Test the stated contract and owner before reading the implementation as an
   explanation of itself.
2. Report findings first, ordered by severity, with a concrete reproduction and
   required outcome.
3. After repairs, verify prior findings and then review the current PR in
   totality.
4. Give one consolidated verdict. Approval accepts this review question, not
   the milestone or unrelated future work.

Use the canonical
[review finding format](../milestones/README.md#review-finding-format).
