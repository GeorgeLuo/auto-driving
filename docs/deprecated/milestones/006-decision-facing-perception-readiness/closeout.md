# Milestone 006 Closeout: Decision-Facing Perception Readiness

Status: **closed 2026-09-18 by explicit operator override**

M006 is accepted as a completed shadow-only milestone. The operator chose to
close the milestone without expanding the live evidence package into a full
per-environment regression matrix. The unrun evidence cases are retained below
as residual risk; they are not represented as passed.

## Outcome

M006 delivered and merged:

- one immutable decision-data boundary for observation, retained evidence,
  patterns, projections, capabilities, timing, and prior host output;
- the bounded `avoid_recent_obstruction` proposal, deterministic selection,
  explicit shadow authority, and proposed-versus-applied separation;
- stage, inspect, stream, replay, and generation-bound combined decision-view
  surfaces with exact source references and images;
- PiRacer host-boundary telemetry with source identity, user/pilot values,
  pre-drivetrain observation, fail-closed joins, and bounded interval coverage;
- captured Chase and stationary PiRacer evidence, including a right-side
  `steer_away_right_obstruction` witness with proposed `-0.35 / 0`, authorized
  `0 / 0`, and `proposed_applied=false`.

The cumulative review surface is PR [#70](https://github.com/GeorgeLuo/auto-driving/pull/70).
Its merge is the terminal M006 delivery action. No successor milestone,
movement milestone, or prediction pre-plan is activated.

## Acceptance judgment

| Area | Judgment |
| --- | --- |
| M006-01–05 | Met by the merged implementation and deterministic validation. |
| M006-06 | Met by operator override. Shared surfaces, provenance, selected intent, and zero applied control are demonstrated; broader live lifecycle coverage is residual. |
| M006-07 | Met by operator override. Chase evaluator isolation and bounded stationary PiRacer zero-output intervals are retained; universal temporal coverage is not claimed. |
| M006-08 | Met by this closeout. The remaining uncertainty is explicitly accepted and no successor is activated. |

## Accepted residual risks

- A complete left/right active matrix was not captured in every environment.
- No single live capture demonstrates fresh → retained → stale → inactive in
  every environment without reset.
- Absent/expired live witnesses and a PiRacer two-pass replay were not fully
  captured.
- Recurring evidence IDs do not establish physical-object identity; bounded
  memory is not a trajectory; image motion does not establish self-motion.
- Physical perception can miss or misclassify evidence under different optics,
  lighting, placement, and timing.
- Authority remains shadow-only. This milestone does not establish movement
  safety, collision avoidance, navigation, prediction, or actuator feedback.

These are accepted limits of this milestone, not hidden implementation claims.
If later work changes proposal lifecycle, host telemetry, viewer identity, or
enables applied movement, it should run targeted deterministic tests and a
focused live smoke check for the changed boundary.

## Validation

- Evidence packet verification passed, including derived HTML and mutation
  rejection.
- Host telemetry consumer tests passed, including bounded history coverage and
  provider-clock-skew retry behavior.
- Python compilation and repository diff checks passed before the closeout
  merge.
- The broader historical test suite retained its previously documented,
  unrelated failures; M006 closeout does not claim a green full suite.

## Final decision

The shadow proposal and telemetry surfaces are useful and sufficiently
inspectable for the declared M006 outcome. The operator accepts the residual
live-evidence gaps and closes the milestone. Future repository work proceeds
through direct focused PRs rather than additional M006 frontiers.
