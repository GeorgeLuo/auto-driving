# Repair Cycle

**When to load:** When addressing review findings or re-reviewing a PR after
repairs.

**Authority:** This summarizes
[Repair Cycle](../milestones/README.md#repair-cycle) and
[Author Repair Response](../milestones/README.md#author-repair-response) in the
canonical contract. The contract wins if any wording conflicts.

## Author

Treat one consolidated changes-requested verdict followed by its repair revision
as one cycle. Before requesting re-review, add one consecutive row to the PR
body's `Repair Cycle Ledger` with the verdict receipt, reviewer-owned `minor` or
`substantial` classification and highest severity, full repair revision, and
contract impact.

Repair the failure class at its owner, not only the reported example. Re-check
prior findings and the accepted matrix. Do not invent a new adversarial pass.
There is no cycle-count stop.

Same-account review cannot use GitHub `CHANGES_REQUESTED`. An unedited
`COMMENTED` review containing only `## Contract Review Receipt` and
`Outcome: accepted` or `changes_requested` is the verdict. Other comments are
concerns and do not force action.

## Reviewer

Verify each prior finding against the repair evidence, then review the current
diff against the accepted proposal. Raise P0–P2 only when the case is in the
accepted matrix or falsifies the stated review question. Everything else is P3
or a later want.

Classify a cycle as substantial when its verdict contains a P0–P2 contract
failure or its repair changes the review question, contract, primary owner or
abstraction, material scope or file impact, external assumption, or adversarial
failure class.
