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
     `completed`, a durable human decision receipt, an operator or meta-manager
     decision owner/time, one route from
     `continue-current-unit`, `replan-current-unit`,
     `proposal-amendment`,
     `split-or-replace-review-unit`, or `abandon-review-unit`, and a disposition
     beginning with `route=<selected-route>;`. A continuation route also needs
     the completed topology audit below. -->

- Status: `not-required`
- Decision receipt: None
- Decision owner/role: None
- Decision time: None
- Route: None
- Disposition: Continue under the current review question.

## Repair Continuation Audit

<!-- Leave this section at its defaults until a continuation or replacement
     decision is needed. For every substantial cycle after the second, replace
     the defaults with a renewed audit for the current substantial cycle. A
     continuation requires unchanged contract/question/owner/abstraction, a
     coherent diff, disposed findings, visible cumulative history, and a fresh
     independent-context review receipt. A replacement route must link its
     lineage decision; it may not use a new PR number as evidence. -->

- Status: `not-required`
- Audited substantial cycle: None
- Continuation receipt: None
- Accepted contract: None
- Primary question: None
- Enforcement owner/abstraction: None
- Coherent diff: None
- Prior findings: None
- Cumulative history: None
- Fresh-context review: None
- Replacement lineage: None
- Risk disposition: None

### Prior Finding Dispositions

| Finding | Disposition |
| --- | --- |
| None | None |

## Final Validation

<!-- Added only during closeout -->

## Topology

- Milestone branch: `milestone/<number>-<slug>`
- Targets: `main` (this cumulative PR only)
- Child review units target the milestone branch, not `main`
