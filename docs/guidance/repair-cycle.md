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
classification and highest severity, full repair revision, and contract impact.
Use the exact unedited GitHub review URL. The review's attached head and stable
`[P0]`–`[P3]` inline-comment URLs—not author prose—own the finding manifest.
Put exactly one `Classification: minor` or `Classification: substantial` line in
the consolidated verdict body, and begin every attached inline finding with its
severity.

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
canonical exact-head GitHub decision review. Its currently authorized actor must
declare either an independent-account or same-account-fresh-context basis. A
second canonical fresh-context totality review must follow it on the same head
with the same explicit actor-basis rule. Append both receipts and the
GitHub-owned actor/time to `Repair Escalation`, copy the decision topology into
`Repair Continuation Audit`, and list the exact cumulative finding set in `Prior
Finding Dispositions`. Bind resolved findings to full repair revisions and the
decision receipt; a carried-forward finding must name its next repair path.
Preserve every prior row and use distinct receipts for every later substantial
cycle. A reviewer-owned P0 invokes this stop immediately and requires a risk
disposition.

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
`abandon-review-unit`. Confirm each append-only escalation row matches the exact
GitHub decision actor, submission time, audited head, route, cumulative manifest,
and fresh-context receipt. A third or later substantial cycle can remain in that
review unit only when distinct renewed receipts and the topology audit prove the
same singular contract, owner, abstraction, coherent diff, and explicit exact
finding dispositions. The final completion handoff, not the continuation
authorization, requires the exact finding set to be resolved. Replacement
lineage remains governed by issue #118; its machine route currently fails
closed, and a new PR number is not evidence of reviewability.
