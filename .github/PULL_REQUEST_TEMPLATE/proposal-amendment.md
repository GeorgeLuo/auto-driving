# <Frontier proposal amendment>

## Milestone Context

- Milestone:
- Base branch: `milestone/<number>-<slug>`
- Amendment branch: `m<number>/amend-<slug>`
- Frontier:
- Accepted proposal PR and merge commit:
- Amendment artifact: `docs/milestones/<number>-<slug>/proposals/<slug>-amendment.md`

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
