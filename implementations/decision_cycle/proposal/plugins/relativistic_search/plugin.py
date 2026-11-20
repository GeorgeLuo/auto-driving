"""Propose smooth action-time arcs toward less visited space."""

from __future__ import annotations

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.evidence import RetainedEvidence
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.proposal.values import ActionProposal, ProposedVehicleCommand, SourceRef
from implementations.decision_cycle.memory.plugins.relativistic_trail.trail import (
    TRAIL_KEY,
    TrailPublication,
    advance,
    contact_pressure,
    polarity_multiplier,
    read_relativistic_trail,
    steering_multiplier,
    visit_density,
    yaw_rate,
)
from implementations.decision_cycle.proposal.shared.obstruction import OBSTRUCTION_KINDS, bbox_bottom, lateral_side

CRUISE_THROTTLE = 0.60
STEERING_TARGETS = (0.0, -0.30, 0.30, -0.55, 0.55, -0.85, 0.85)
LOOKAHEAD_STEP_S = 0.18
LOOKAHEAD_STEPS = 8
STEERING_RATE_PER_S = 1.15
OBSTRUCTION_MAX_AGE_MS = 700


def _slew(previous: float, target: float, limit: float) -> float:
    return min(previous + limit, max(previous - limit, target))


def _arc_cost(trail: TrailPublication, previous: float, target: float, obstruction: float) -> float:
    now, pose = trail["at_ms"], trail["pose"]
    response, throttle_sign = trail["turn_response"], trail["throttle_sign"]
    steering_sign = steering_multiplier(response, throttle_sign)
    throttle = CRUISE_THROTTLE if response is None else CRUISE_THROTTLE * polarity_multiplier(throttle_sign)
    steer = previous
    visits, contacts = 0.0, 0.0
    # Each step starts at the realized steering and models the approach to the target.
    for step in range(LOOKAHEAD_STEPS):
        next_steer = _slew(steer, target, LOOKAHEAD_STEP_S * STEERING_RATE_PER_S)
        omega = yaw_rate(response, steering_sign * (steer + next_steer) / 2, throttle)
        pose = advance(pose, LOOKAHEAD_STEP_S, LOOKAHEAD_STEP_S * omega)
        steer = next_steer
        if step >= 2:
            visits += visit_density(trail["visited"], pose, now)
        contacts += contact_pressure(trail["contacts"], pose, now)
    return (
        visits / (LOOKAHEAD_STEPS - 2) + 4.0 * contacts / LOOKAHEAD_STEPS + obstruction * target
        + 0.45 * target ** 2 + 0.16 * abs(target - previous)
    )


def _foreground_obstruction(
    raw: object, now: int,
) -> tuple[float, RetainedEvidence | None]:
    if not isinstance(raw, (tuple, list)):
        return 0.0, None
    sides: dict[str, tuple[float, RetainedEvidence | None]] = {"left": (0.0, None), "right": (0.0, None)}
    for record in raw:
        if not isinstance(record, RetainedEvidence) or record.kind not in OBSTRUCTION_KINDS:
            continue
        if record.location is None or record.location.frame != "image":
            continue
        age = now - record.origin.updated_at_ms
        side = lateral_side(record)
        if side is None or not 0 <= age < OBSTRUCTION_MAX_AGE_MS:
            continue
        # Bboxes low in the image are stronger cues. A zone-only observation is usable.
        bottom = bbox_bottom(record)
        foreground = 0.45 if bottom < 0.0 else min(1.0, max(0.0, (bottom - 0.45) / 0.40))
        strength = (record.confidence or 0.0) * foreground * (1.0 - age / OBSTRUCTION_MAX_AGE_MS)
        if strength > sides[side][0]:
            sides[side] = strength, record
    left, left_record = sides["left"]
    right, right_record = sides["right"]
    if left == right:
        return 0.0, None
    return 1.75 * (right - left), right_record if right > left else left_record


def _steering_limit(trail: TrailPublication, direction: int) -> float:
    interval_s = trail["interval_s"]
    if trail["escape"] is not None or direction * trail["applied_direction"] < 0:
        return min(0.62, 4.0 * max(interval_s, 0.03))
    # Time-based slew keeps changes small at high rates while matching the rollout at slow rates.
    return min(0.36, STEERING_RATE_PER_S * max(interval_s, 0.02))


def _escape_steering(previous: float, target: float, direction: int, applied_direction: int) -> float:
    # Preserve body yaw through a reversal: the same wheel angle turns the
    # body the opposite way when travel direction changes.
    if direction * applied_direction < 0:
        previous = -previous
    return 0.0 if previous * target < 0.0 else previous


class RelativisticSearch:
    plugin_id = "relativistic_search"

    def propose(self, context: DecisionFrameContext) -> ActionProposal:
        shared_memory = context.shared_memory
        trail = read_relativistic_trail(shared_memory)
        if trail is None or shared_memory is None:
            return ActionProposal(
                self.plugin_id, context.frame_id, "missing_input", "none", None, "trail_unavailable", None,
            )
        steering_sign = steering_multiplier(trail["turn_response"], trail["throttle_sign"])
        previous = (trail["applied_steering"] or 0.0) * steering_sign
        escape = trail["escape"]
        refs = (SourceRef("memory_record", TRAIL_KEY, plugin_id="relativistic_trail"),)
        if escape is None:
            obstruction, record = _foreground_obstruction(shared_memory.get(EVIDENCE_KEY), trail["at_ms"])
            target = min(STEERING_TARGETS, key=lambda steer: _arc_cost(trail, previous, steer, obstruction))
            direction, reason = 1, "seek_less_visited_space"
            if record is not None:
                refs += (SourceRef("memory_record", record.record_id, frame_id=record.origin.frame_id,
                                   observation_id=record.origin.observation_id,
                                   plugin_id=record.origin.source_plugin_id, note="lateral_obstruction"),)
        else:
            direction = escape["direction"]
            magnitude = 0.70 if escape["phase"] == "back_out" else 1.0
            target = direction * escape["turn_side"] * magnitude
            previous = _escape_steering(previous, target, direction, trail["applied_direction"])
            reason = escape["phase"]
        steer = _slew(previous, target, _steering_limit(trail, direction))
        throttle = direction * polarity_multiplier(trail["throttle_sign"]) * CRUISE_THROTTLE
        return ActionProposal(
            self.plugin_id, context.frame_id, "fresh", "fresh", 0.8, reason,
            ProposedVehicleCommand(steer * steering_sign, throttle), source_refs=refs, available=True,
        )
