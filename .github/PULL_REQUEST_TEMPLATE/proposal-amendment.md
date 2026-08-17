# <Frontier proposal amendment>

## Milestone Context

- Milestone:
- Base branch: `milestone/<number>-<slug>`
- Amendment branch: `m<number>/amend-<slug>`
- Frontier:
- Accepted proposal PR and merge commit:
- Amendment artifact: `docs/milestones/<number>-<slug>/proposals/<slug>-amendment.md`

## Review Kind

<!-- One supported value matching the current frontier's canonical milestone
     plan. -->

## Review Question

<!-- Does established evidence justify this bounded correction, and is the
     resulting implementation contract sufficiently owned and testable? -->

## Evidence Requiring Amendment

<!-- Link the established run, test, review finding, or other durable evidence. -->

## Contract Delta

<!-- State exactly what changes. The original proposal remains immutable. -->

## Invariant Contractability

- Universal or deterministic claim introduced or changed (`yes` / `no`):
- [ ] If `yes`, the amendment artifact completes `Trust And Authority Model` for the changed trust boundary or authority mapping.
- [ ] If `yes`, the amendment artifact completes `Evidence Topology And Capture Strategy` for changed derivation, verification, or capture readiness.
- [ ] If `yes`, uncertain process, library, and external-system boundaries have feasibility evidence or narrow the amended claim as an unverified limit.

## Independence Check

- [ ] No accepted proposal or prior amendment was modified.
- [ ] No product or runtime implementation changed.
- [ ] No implementation tests or generated runtime artifacts were added.
- [ ] The new amendment, plan transition, and generated plan HTML are the only changes.
- [ ] The proposal's reviewed `Expected Handoff` is unchanged.

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

## Review Notes

<!-- Amendment sections needing deepest attention. Before merge, a reviewer
     with current repository push authority must submit a GitHub review on the
     final amendment commit. An APPROVE review counts. For self-review, submit
     a new, unedited COMMENT review containing only:

     ## Contract Review Receipt

     - Outcome: `accepted`

     Use `changes_requested` instead when the contract is not acceptable.
     A PR conversation comment does not count. Any later commit invalidates
     this receipt. Every authorized reviewer's latest exact-head decision must
     be clear of outstanding changes.
-->
