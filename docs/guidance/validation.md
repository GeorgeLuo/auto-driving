# Validation

**When to load:** When designing, running, or reporting validation for a
proposal, implementation, repair, evidence unit, or documentation change.

**Authority:** This summarizes validation requirements throughout the canonical
[Milestone Planning And Delivery Contract](../milestones/README.md). The
contract wins if any wording conflicts.

## Sequence

1. Run focused tests for the changed owner and reported failure class.
2. Run the broader deterministic suite required by the accepted proposal.
3. Run milestone workflow and documentation validation when those surfaces
   changed.
4. Check formatting, generated artifacts, and the final externally visible
   representation.
5. Run live or external checks only when the review question requires them;
   record environmental assumptions and non-claims.

## Evidence

Report exact commands, pass/fail status, test counts, skips, and relevant
artifacts. Do not translate an unrun check into a claim. Update the PR
description after repairs so reviewers do not have to reconstruct current
evidence from comments or commit history.

For universal claims, validate the final value after normalization, storage,
serialization, or transport, not only the first internal representation.
