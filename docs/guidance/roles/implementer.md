# Implementer Role

**When to load:** When the requested operation is proposal authoring,
implementation, repair, building, or changing repository artifacts.

**Authority:** This role is derived from the phase and delivery rules in the
canonical
[Milestone Planning And Delivery Contract](../../milestones/README.md). The
contract wins if any wording conflicts.

## Mindset

Produce the smallest complete deliverable that answers the accepted review
question. Prefer existing ownership boundaries and explicit validation over
new framework surface. Do not change the acceptance contract while
implementing it.

## Phase

- In `ready_for_proposal`, author the proposal and required plan transition
  only. Load [proposal-vs-implementation.md](../proposal-vs-implementation.md)
  and [review-unit.md](../review-unit.md).
- In `ready_for_implementation`, implement only the accepted proposal. Load
  [proposal-vs-implementation.md](../proposal-vs-implementation.md) and
  [validation.md](../validation.md).
- When addressing findings in the existing PR, load
  [repair-cycle.md](../repair-cycle.md), [validation.md](../validation.md), and
  the relevant adversarial cases.
- When a human requests a change from hands-on testing during
  `implementation_in_review`, classify it before editing and load
  [hitl-implementation-adjunct.md](../hitl-implementation-adjunct.md). An
  adjunct is available only when the accepted parent contract remains true
  without the additive change.

Stop and report the required handoff when the next action belongs to another
phase or role. Never combine proposal acceptance and implementation merely
because the same agent can perform both.
