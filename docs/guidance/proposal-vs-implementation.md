# Proposal And Implementation

**When to load:** When authoring or reviewing a proposal, handing accepted work
to an implementer, starting implementation, or deciding what phase permits.

**Authority:** This summarizes
[Proposal And Implementation Are Separate](../milestones/README.md#proposal-and-implementation-are-separate)
in the canonical contract. The contract wins if any wording conflicts.

## Phase Boundary

- `ready_for_proposal`: proposal work may start; product implementation may not.
- `proposal_in_review`: change the proposal and required plan transition only.
- `ready_for_implementation`: implement only the exact accepted proposal.
- `implementation_in_review`: reconcile product, tests, and documentation to
  that accepted scope.

Run the milestone workflow status command instead of inferring the phase from
conversation history.

## Handoffs

The reviewer stops when a phase is ready and states the next permitted role.
The operator assigns proposal authorship or implementation explicitly. A person
or model may fill both roles, but only in separate branches and review phases.

A proposal records the contract, owner, affected paths, adversarial matrix,
assumptions, non-goals, file impact, validation plan, and expected handoff. It
contains no implementation.

Implementation links the accepted proposal and merge commit, stays within that
contract, and reports actual file impact and validation. If the contract must
change, return to proposal review rather than rewriting acceptance during
implementation.
