# Milestone <number> — <title>

## Objective

<!-- Stable milestone objective; link the plan for detail -->

## Completion Usage

See the milestone plan completion-usage table:
`docs/milestones/<number>-<slug>/plan.md`

## Accepted Review Units

- #

## Exit Criteria

Authoritative table in the milestone plan (do not duplicate row-by-row status here).

## Current Status

<!-- Active / ready for closeout / ready for final review -->

## Unresolved Risks

<!-- Link the plan Open Risks section or list only residual blockers -->

## Repair Cycle Ledger

<!-- Add one row for each consolidated changes-requested verdict followed by a
     repair revision and re-review request. Classification is `minor` or
     `substantial`; the reviewer owns disputed classifications. Keep the sole
     all-None row until the first repair cycle. -->

| Cycle | Review receipt | Classification | Repair revision | Contract impact |
| --- | --- | --- | --- | --- |
| None | None | None | None | None |

## Repair Escalation

<!-- At the second substantial cycle, replace these defaults with Status
     `completed`, a durable human decision receipt, one route from
     `replan-current-unit`, `proposal-amendment`,
     `split-or-replace-review-unit`, or `abandon-review-unit`, and the resulting
     disposition. A third substantial cycle cannot remain in this review unit. -->

- Status: `not-required`
- Decision receipt: None
- Route: None
- Disposition: Continue under the current review question.

## Final Validation

<!-- Added only during closeout -->

## Topology

- Milestone branch: `milestone/<number>-<slug>`
- Targets: `main` (this cumulative PR only)
- Child review units target the milestone branch, not `main`
