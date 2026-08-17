# Review Repair — <original review unit>

## Original Review Question

## Why A Separate Repair PR

<!-- Only use a separate PR when remaining in the original PR is not possible -->

## Original Invariant Or Contract

## Bypass

## Root Cause

## Owning Boundary Changed

## Adjacent Paths Audited

## Regression Coverage

## Remaining Assumptions

## Validation

```text
```

## Fresh Adversarial Pass

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
     reviewer must differ from the PR author; receipt identity, time, route,
     head, cumulative finding manifest, and chronology are machine-verified.
     `split-or-replace-review-unit` currently fails closed pending #118. -->

| Substantial cycle | Decision receipt | Decision owner | Decision role | Decision time | Route | Audited head | Fresh-context review | Finding manifest | Disposition |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| None | None | None | None | None | None | None | None | None | None |

## Repair Continuation Audit

<!-- Append one audit row for every required escalation row and copy its
     topology fields from the canonical decision review. Never rewrite an older
     row. Same-unit continuation requires unchanged contract, singular question,
     unchanged owner/abstraction, coherent diff, and every exact finding resolved.
     A reviewer-owned P0 also requires a non-None risk disposition. -->

| Substantial cycle | Decision receipt | Accepted contract | Primary question | Enforcement owner/abstraction | Coherent diff | Prior findings | Cumulative history | Replacement lineage | Risk disposition |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| None | None | None | None | None | None | None | None | None | None |

### Prior Finding Dispositions

| Substantial cycle | Finding | Disposition | Repair revision | Disposition receipt |
| --- | --- | --- | --- | --- |
| None | None | None | None | None |
