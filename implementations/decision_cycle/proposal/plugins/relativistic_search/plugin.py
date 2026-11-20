"""Propose smooth action-time arcs as the sole search proposal."""

from __future__ import annotations

from dataclasses import dataclass

from autonomy.decision_cycle.action.publication import read_control_applications
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.evidence import RetainedEvidence
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.proposal.values import ActionProposal, ProposedVehicleCommand, SourceRef
from autonomy.shared_memory import SharedMemory
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
from implementations.decision_cycle.proposal.shared.obstruction import OBSTRUCTION_KINDS, bbox_bottom, horizontal_span

CRUISE_THROTTLE = 0.60
STEERING_TARGETS = (0.0, -0.25, 0.25, -0.50, 0.50, -0.75, 0.75, -1.0, 1.0)
LOOKAHEAD_STEP_S = 0.12
LOOKAHEAD_STEPS = 9
TURN_STEPS = 4
STEERING_RATE_PER_S = 1.4
AVOIDANCE_RATE_PER_S = 6.0
OBSTRUCTION_MAX_AGE_MS = 180


@dataclass(frozen=True)
class Obstruction:
    start: float
    end: float
    pressure: float
    record: RetainedEvidence

    def cost(self, target: float) -> float:
        # Probe an image corridor toward the turn. Use a soft margin to
        # steer away before the predicted corridor overlaps the boundary.
        x = 0.5 + 0.48 * target
        distance = max(self.start - x, x - self.end, 0.0)
        overlap = max(0.0, 1.0 - distance / 0.23)
        return self.pressure * overlap


def _slew(previous: float, target: float, limit: float) -> float:
    return min(previous + limit, max(previous - limit, target))


def _arc_cost(trail: TrailPublication, previous: float, target: float, steering_rate: float) -> float:
    now, pose = trail["at_ms"], trail["pose"]
    response, throttle_sign = trail["turn_response"], trail["throttle_sign"]
    steering_sign = steering_multiplier(response, throttle_sign)
    throttle = CRUISE_THROTTLE if response is None else CRUISE_THROTTLE * polarity_multiplier(throttle_sign)
    steer = previous
    visits, contacts = 0.0, 0.0
    # Each step starts at the realized steering and models the approach to the target.
    for step in range(LOOKAHEAD_STEPS):
        step_target = target if step < TURN_STEPS else 0.0
        next_steer = _slew(steer, step_target, LOOKAHEAD_STEP_S * steering_rate)
        omega = yaw_rate(response, steering_sign * (steer + next_steer) / 2, throttle)
        pose = advance(pose, LOOKAHEAD_STEP_S, LOOKAHEAD_STEP_S * omega)
        steer = next_steer
        if step >= 2:
            visits += min(4.0, visit_density(trail["visited"], pose, now))
        contacts += contact_pressure(trail["contacts"], pose, now)
    return (
        0.85 * visits / (LOOKAHEAD_STEPS - 2) + 3.5 * contacts / LOOKAHEAD_STEPS
        + 1.1 * target ** 2 + 0.35 * abs(target - previous)
    )


def _foreground_obstructions(raw: object, now: int) -> list[Obstruction]:
    obstructions = []
    if not isinstance(raw, (tuple, list)):
        return obstructions
    for record in raw:
        if not isinstance(record, RetainedEvidence) or record.kind not in OBSTRUCTION_KINDS:
            continue
        age = now - record.origin.updated_at_ms
        span = horizontal_span(record)
        if span is None or not 0 <= age < OBSTRUCTION_MAX_AGE_MS:
            continue
        bottom = bbox_bottom(record)
        foreground = _foreground_strength(record, bottom)
        strength = (record.confidence or 0.0) * foreground * (1.0 - age / OBSTRUCTION_MAX_AGE_MS)
        if strength > 0.0:
            obstructions.append(Obstruction(*span, strength, record))
    # An earlier view can fill in missing evidence, but a newer view of the
    # same detector supersedes its coordinates as the camera moves.
    latest = {cue.record.origin.source_plugin_id: cue.record.origin.updated_at_ms for cue in
              sorted(obstructions, key=lambda item: item.record.origin.updated_at_ms)}
    return [cue for cue in obstructions
            if cue.record.origin.updated_at_ms == latest[cue.record.origin.source_plugin_id]]


def _foreground_strength(record: RetainedEvidence, bottom: float) -> float:
    if bottom >= 0.0:
        return min(1.0, max(0.0, (bottom - 0.415) / 0.19))
    location = record.location
    zone = "" if location is None else location.zone
    if zone.startswith("far_"):
        return 0.0
    return 0.7 if zone.startswith("near_") else 0.35


def _search_target(
    trail: TrailPublication, previous: float, raw_evidence: object,
) -> tuple[float, float, Obstruction | None]:
    obstructions = _foreground_obstructions(raw_evidence, trail["at_ms"])
    strongest = max(obstructions, key=lambda cue: cue.cost(0.0), default=None)
    pressure = 0.0 if strongest is None else strongest.cost(0.0)
    rate = STEERING_RATE_PER_S + (AVOIDANCE_RATE_PER_S - STEERING_RATE_PER_S) * min(1.0, pressure * 3.0)

    def score(target: float) -> float:
        obstruction = max((cue.cost(target) for cue in obstructions), default=0.0)
        return _arc_cost(trail, previous, target, rate) + 11.0 * obstruction

    return min(STEERING_TARGETS, key=score), rate, strongest


def _steering_limit(
    trail: TrailPublication, direction: int, applied_direction: int, steering_rate: float,
) -> float:
    interval_s = trail["interval_s"]
    if trail["escape"] is not None or direction * applied_direction < 0:
        return min(0.62, 4.0 * max(interval_s, 0.03))
    # Time-based slew keeps changes small at high rates while matching the rollout at slow rates.
    return min(0.55, steering_rate * max(interval_s, 0.02))


def _escape_steering(previous: float, target: float, direction: int, applied_direction: int) -> float:
    # Preserve body yaw through a reversal: the same wheel angle turns the
    # body the opposite way when travel direction changes.
    if direction * applied_direction < 0:
        previous = -previous
    return 0.0 if previous * target < 0.0 else previous


def _latest_steering(trail: TrailPublication, shared_memory: SharedMemory) -> tuple[float, int]:
    # The trail integrates controls at capture time. Slew from the latest
    # confirmed write so delayed images cannot rewind the commanded steering.
    applications = read_control_applications(shared_memory)
    if applications:
        latest = applications[-1]
        if latest.applied and latest.mode == "autonomy":
            direction = (latest.control.throttle > 0.1) - (latest.control.throttle < -0.1)
            return latest.control.steering, direction * polarity_multiplier(trail["throttle_sign"])
    return trail["applied_steering"] or 0.0, trail["applied_direction"]


class RelativisticSearch:
    """Arm this in place of avoid_recent_obstruction with the highest-confidence plan."""

    plugin_id = "relativistic_search"

    def propose(self, context: DecisionFrameContext) -> ActionProposal:
        shared_memory = context.shared_memory
        trail = read_relativistic_trail(shared_memory)
        if trail is None or shared_memory is None:
            return ActionProposal(
                self.plugin_id, context.frame_id, "missing_input", "none", None, "trail_unavailable", None,
            )
        steering_sign = steering_multiplier(trail["turn_response"], trail["throttle_sign"])
        previous, applied_direction = _latest_steering(trail, shared_memory)
        previous *= steering_sign
        escape = trail["escape"]
        refs = (SourceRef("memory_record", TRAIL_KEY, plugin_id="relativistic_trail"),)
        steering_rate = STEERING_RATE_PER_S
        if escape is None:
            target, steering_rate, strongest = _search_target(trail, previous, shared_memory.get(EVIDENCE_KEY))
            direction, reason = 1, "seek_less_visited_space"
            if strongest is not None:
                record = strongest.record
                refs += (SourceRef("memory_record", record.record_id, frame_id=record.origin.frame_id,
                                   observation_id=record.origin.observation_id,
                                   plugin_id=record.origin.source_plugin_id, note="foreground_obstruction"),)
        else:
            direction, reason = escape["direction"], escape["phase"]
            target = direction * escape["turn_side"] * (0.70 if reason == "back_out" else 1.0)
            previous = _escape_steering(previous, target, direction, applied_direction)
        steer = _slew(previous, target, _steering_limit(trail, direction, applied_direction, steering_rate))
        throttle = direction * polarity_multiplier(trail["throttle_sign"]) * CRUISE_THROTTLE
        return ActionProposal(
            self.plugin_id, context.frame_id, "fresh", "fresh", 0.8, reason,
            ProposedVehicleCommand(steer * steering_sign, throttle), source_refs=refs, available=True,
        )
