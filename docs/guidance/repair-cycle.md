# Repair Cycle

**When to load:** When addressing review findings or re-reviewing a PR after
repairs.

**Authority:** This summarizes
[Repair Cycle](../milestones/README.md#repair-cycle) and
[Author Repair Response](../milestones/README.md#author-repair-response) in the
canonical contract. The contract wins if any wording conflicts.

## Author

For each finding, record:

- root cause;
- owning boundary changed;
- adjacent paths audited;
- regression coverage;
- remaining assumptions.

Repair the failure class at its owner, not only the reported example. Reconcile
the PR description and exact validation results to the new diff, then perform a
fresh adversarial pass before requesting re-review.

## Reviewer

Verify each prior finding against the repair evidence, then review the complete
current diff for regressions and newly exposed bypasses. Distinguish incomplete
repairs from genuinely new findings in the verdict.

One repair cycle is normal. After two substantial cycles for the same invariant,
reconsider the abstraction, enforcement location, scope, and whether the review
question is actually singular.
