# Repair Cycle

**When to load:** When addressing review findings or re-reviewing a PR after
repairs.

**Authority:** This summarizes
[Repair Cycle](../milestones/README.md#repair-cycle) and
[Author Repair Response](../milestones/README.md#author-repair-response) in the
canonical contract. The contract wins if any wording conflicts.

## Author

Treat one consolidated changes-requested verdict followed by its repair revision
as one cycle, regardless of the number of findings, comments, or commits. Before
requesting re-review, add one consecutive row to the PR body’s `Repair Cycle
Ledger` with the verdict receipt, reviewer-owned `minor` or `substantial`
classification, repair revision, and contract impact.

For each finding, record:

- root cause;
- owning boundary changed;
- adjacent paths audited;
- regression coverage;
- remaining assumptions.

Repair the failure class at its owner, not only the reported example. Reconcile
the PR description and exact validation results to the new diff, then perform a
fresh adversarial pass before requesting re-review.

Do not self-downgrade a disputed or missing classification; treat it as
substantial until the reviewer resolves it. At the second substantial cycle,
stop before re-review and hand the unit to the operator or meta-manager for a
durable escalation decision. The implementing or repair author cannot supply
that decision alone.

## Reviewer

Verify each prior finding against the repair evidence, then review the complete
current diff for regressions and newly exposed bypasses. Distinguish incomplete
repairs from genuinely new findings in the verdict.

Classify a cycle as substantial when its verdict contains a P0–P2 contract
failure or its repair changes the review question, contract, primary owner or
abstraction, material scope or file impact, external assumption, or adversarial
failure class. A cycle is minor only for editorial, evidence-formatting, or
localized P3 work that changes none of those surfaces.

One substantial repair cycle is normal. At the second, require one recorded
route before re-review: `replan-current-unit`, `proposal-amendment`,
`split-or-replace-review-unit`, or `abandon-review-unit`. Confirm the PR body’s
`Repair Escalation` names the human decision receipt and resulting disposition.
A third substantial cycle cannot remain in that review unit; close or supersede
it through the selected route, preserving a link to its history.

When the recorded route is `proposal-amendment`, start the amendment with
`workflow.py start-proposal-amendment` even if the frontier is already
`implementation_in_review`. Pass the escalation receipt and mark the
implementation PR `paused`/`reconcile` or `closed`/`replace`. Do not merge or
re-review the implementation under the unamended proposal.
