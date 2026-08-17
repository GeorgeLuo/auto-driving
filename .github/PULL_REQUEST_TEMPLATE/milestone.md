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
     repair revision. Classification, highest severity, and the exact finding
     manifest come from the linked GitHub review. Use full 40-character repair
     SHAs. Keep the sole all-None row until the first repair cycle. -->

| Cycle | Review receipt | Classification | Highest severity | Repair revision | Contract impact |
| --- | --- | --- | --- | --- | --- |
| None | None | None | None | None | None |

## Repair Escalation

<!-- At the second substantial cycle or any P0, append a row copied from an
     unedited canonical GitHub decision review on the exact repaired head.
     Preserve every prior row. The authorized decision actor and fresh-context
     reviewer must declare an actor basis; same-account fresh-context agents are
     allowed when that basis is explicit. Receipt identity, time, route,
     head, cumulative finding manifest, and chronology are machine-verified.
     `split-or-replace-review-unit` currently fails closed pending #118. -->

| Substantial cycle | Decision receipt | Decision owner | Decision role | Decision time | Route | Audited head | Fresh-context review | Finding manifest | Disposition |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| None | None | None | None | None | None | None | None | None | None |

## Repair Continuation Audit

<!-- Append one audit row for every required escalation row and copy its
     topology fields from the canonical decision review. Never rewrite an older
     row. Same-unit continuation requires unchanged contract, singular question,
     unchanged owner/abstraction, coherent diff, and an explicit disposition for
     every exact finding. Deferred or carried-forward findings may remain open
     during authorized continuation; completion still requires every finding
     resolved.
     A reviewer-owned P0 also requires a non-None risk disposition. -->

| Substantial cycle | Decision receipt | Accepted contract | Primary question | Enforcement owner/abstraction | Coherent diff | Prior findings | Cumulative history | Replacement lineage | Risk disposition |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| None | None | None | None | None | None | None | None | None | None |

### Prior Finding Dispositions

| Substantial cycle | Finding | Disposition | Repair revision | Disposition receipt |
| --- | --- | --- | --- | --- |
| None | None | None | None | None |

## Final Validation

<!-- Added only during closeout -->

## Topology

- Milestone branch: `milestone/<number>-<slug>`
- Targets: `main` (this cumulative PR only)
- Child review units target the milestone branch, not `main`
