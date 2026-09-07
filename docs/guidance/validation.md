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

## Test design

Use the canonical [testing purpose and regression value](../milestones/README.md#testing-purpose-and-regression-value)
rule: identify new or materially changed tests as `consumer`, `boundary`, or
justified `mechanism` in a name or short note, independent of owner/layer.
Make the concrete regression clear, prefer public entry points and observable
results, and replace assignment-level tautologies. Meaningful field checks
across schema, normalization, serialization, transport, or consumer-output
boundaries remain useful. Cover normal usage and contracted boundaries without
expanding the accepted matrix during repair. No suite migration or tagging
framework is required.

## Evidence

Report exact commands, pass/fail status, test counts, skips, and relevant
artifacts. Do not translate an unrun check into a claim. Update the PR
description after repairs so reviewers do not have to reconstruct current
evidence from comments or commit history.

If derived evidence HTML is committed, it must be regenerable from the
committed frontier record it presents, not from a fixture that is not that
record, and live beside that record in the proposal-declared frontier evidence
directory. Do not treat missing HTML, layout, or on-page volume as a validation
failure unless the operator required that page.
