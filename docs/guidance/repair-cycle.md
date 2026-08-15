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
durable escalation decision with an explicit owner, time, route, and
disposition. The implementing or repair author cannot supply that decision
alone. If the selected route is `continue-current-unit`, complete the topology
audit for the current substantial cycle, list every prior finding disposition,
preserve cumulative history, and obtain a fresh-context or independent totality
review receipt. Renew that receipt before every later substantial cycle.

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
route before re-review: `continue-current-unit`, `replan-current-unit`,
`proposal-amendment`, `split-or-replace-review-unit`, or
`abandon-review-unit`. Confirm the PR body’s `Repair Escalation` names the
authorized decision receipt, owner/time, route, and disposition. A third or later
substantial cycle can remain in that review unit only when the renewed topology
audit proves the same singular contract, owner, abstraction, coherent diff, and
disposed findings; otherwise the audit must select amendment, split/replacement,
or abandonment. Replacement lineage remains governed by issue #118, and a new
PR number is not evidence of reviewability.
