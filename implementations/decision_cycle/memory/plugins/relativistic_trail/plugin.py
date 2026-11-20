"""Retain action-time pose, visits and escape state from vehicle dynamics."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TypedDict, cast

from autonomy.decision_cycle.action.publication import control_applications_over
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.observation.publication import read_observation
from autonomy.decision_cycle.perception.evidence.values import PerceptionSignal
from autonomy.runtime.execution import ControlApplication
from autonomy.shared_memory import SharedMemory
from implementations.decision_cycle.perception.shared.serialization.history import history_float

from .trail import (
    CELL_SIZE_S,
    CONTACT_MAX_AGE_MS,
    TRAIL_KEY,
    Contact,
    Escape,
    EscapeCause,
    Polarity,
    Pose,
    TrailPublication,
    TurnResponse,
    VisitedCell,
    advance,
    cell_key,
    contact_pressure,
    decayed_weight,
    polarity_multiplier,
    visit_density,
    yaw_rate,
)

STATE_KEY = "memory.relativistic_trail.state"
CELL_LIMIT = 120
CONTACT_LIMIT = 16
MAX_INTERVAL_MS = 500
BACK_OUT_S = 0.26
BACK_OUT_TIMEOUT_MS = 400
TURN_AWAY_RAD = 1.10
TURN_AWAY_TIMEOUT_MS = 2000
STATIONARY_CONTACT_S = 0.38
UNOBSERVED_TRAVEL_LIMIT_S = 1.6
LOOP_WINDOW_MS = 4000
MOTION_SAMPLE_MS = 200


class MotionSample(Pose):
    at_ms: int
    swept_rad: float


class TrailState(TypedDict):
    publication: TrailPublication
    motion: list[MotionSample]
    swept_rad: float
    last_turn_side: int
    stationary_s: float
    unobserved_s: float


@dataclass(frozen=True)
class Motion:
    distance_s: float
    yaw_rad: float
    drive_direction: int
    drive_s: float
    blocked: bool
    impeded: bool
    stationary: bool
    unobserved: bool


class RelativisticTrail:
    plugin_id = "relativistic_trail"
    contract = MemoryPluginContract(
        description="Retain an action-time trail and contact escape directive.",
        assumptions=("Vehicle dynamics provides visual motion and learns from applied autonomous controls.",),
        limitations=("Relative heading and action-time distance drift as estimates accumulate.",),
    )

    def update(self, context: DecisionFrameContext) -> None:
        shared_memory = context.shared_memory
        if shared_memory is None:
            raise ValueError("relativistic_trail requires shared memory")
        now = context.timestamp_ms
        state = _read_state(shared_memory)
        if state is not None and now == state["publication"]["at_ms"]:
            shared_memory[TRAIL_KEY] = state["publication"]
            return
        if state is None or now < state["publication"]["at_ms"]:
            state = _initial_state(now)
        updated = _update_trail(state, _dynamics_signals(shared_memory), shared_memory, now)
        shared_memory[STATE_KEY] = updated
        shared_memory[TRAIL_KEY] = updated["publication"]

    def status(self, shared_memory: SharedMemory | None) -> dict[str, object]:
        state = _read_state(shared_memory)
        if state is None:
            return {"available": False}
        trail = state["publication"]
        escape = trail["escape"]
        return {
            "available": True,
            "pose": dict(trail["pose"]),
            "visited_cell_count": len(trail["visited"]),
            "recent_contact_count": len(trail["contacts"]),
            "turn_response": None if trail["turn_response"] is None else dict(trail["turn_response"]),
            "throttle_sign": trail["throttle_sign"],
            "escape_phase": None if escape is None else escape["phase"],
            "escape_cause": None if escape is None else escape["cause"],
        }

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        if shared_memory is not None:
            shared_memory.pop(STATE_KEY, None)
            shared_memory.pop(TRAIL_KEY, None)


def _read_state(shared_memory: SharedMemory | None) -> TrailState | None:
    value = None if shared_memory is None else shared_memory.get(STATE_KEY)
    return cast(TrailState, value) if isinstance(value, dict) else None


def _initial_state(now: int) -> TrailState:
    pose: Pose = {"x_s": 0.0, "z_s": 0.0, "heading_rad": 0.0}
    return {
        "publication": {
            "at_ms": now, "interval_s": 0.0, "pose": pose,
            "visited": {cell_key(0.0, 0.0): {"visits": 1, "weight": 1.0, "last_visited_at_ms": now}},
            "contacts": [], "turn_response": None, "throttle_sign": "unknown",
            "applied_steering": None, "applied_direction": 0, "escape": None,
        },
        "motion": [], "swept_rad": 0.0, "last_turn_side": -1, "stationary_s": 0.0, "unobserved_s": 0.0,
    }


def _dynamics_signals(shared_memory: SharedMemory) -> dict[str, PerceptionSignal]:
    # The cycle publishes the observation in shared memory before memory runs.
    observation = read_observation(shared_memory)
    if observation is None:
        return {}
    return {
        signal.signal_id: signal for signal in observation.signals
        if signal.source_plugin_id == "vehicle_dynamics" and signal.signal_id in {
            "visual_yaw_rate_rad_s", "visual_expansion_rate_per_s", "turn_response_available",
        }
    }


def _polarity(value: object) -> Polarity:
    if value == "normal":
        return "normal"
    return "inverted" if value == "inverted" else "unknown"


def _direction(throttle: float) -> int:
    return (throttle > 0.1) - (throttle < -0.1)


def _turn_response(signal: PerceptionSignal | None) -> TurnResponse | None:
    if signal is None or signal.value is not True:
        return None
    props = signal.properties
    kind = props.get("kind")
    gain = history_float(props.get("gain_per_s"))
    angle = history_float(props.get("max_angle_rad"))
    if kind not in ("linear", "ackermann_tan") or gain is None or angle is None:
        return None
    if kind == "ackermann_tan" and not 0.0 < angle < math.pi / 2:
        return None
    return {"kind": kind, "gain_per_s": gain, "max_angle_rad": angle, "turn_sign": _polarity(props.get("turn_sign"))}


def _applied_motion(
    applications: tuple[ControlApplication, ...], response: TurnResponse | None, polarity: int,
    start_ms: int, end_ms: int,
) -> tuple[float, float]:
    """Integrate the covered control segments, including any changes mid-pair."""
    if not applications:
        return 0.0, 0.0
    ends = [app.delivered_at_ms for app in applications[1:]] + [end_ms]
    drive_s, yaw_rad = 0.0, 0.0
    for app, until in zip(applications, ends, strict=True):
        assert until is not None
        span_s = (until - start_ms) / 1000.0
        throttle = app.control.throttle
        direction = _direction(throttle)
        if direction:
            drive_s += span_s * direction * polarity
            if response is not None:
                yaw_rad += span_s * yaw_rate(response, app.control.steering, throttle)
        start_ms = until
    return drive_s, yaw_rad


def _visual_yaw(signal: PerceptionSignal | None, dt_s: float) -> float | None:
    if dt_s <= 0.0 or signal is None or signal.properties.get("confident") is not True:
        return None
    delta = history_float(signal.properties.get("yaw_delta_rad"))
    if delta is not None:
        return delta
    rate = history_float(signal.value)
    return None if rate is None else rate * dt_s


def _sparse_tracks_are_stationary(signal: PerceptionSignal | None) -> bool:
    if signal is None or signal.properties.get("mode") != "abstain_low_tracks":
        return False
    # Too few tracks for a yaw fit can still show a stationary scene.
    count = history_float(signal.properties.get("num_tracks"))
    shift = history_float(signal.properties.get("median_px_shift"))
    return count is not None and count > 0 and shift == 0.0


def _motion(
    trail: TrailPublication, signals: dict[str, PerceptionSignal],
    applications: tuple[ControlApplication, ...], interval_s: float, now: int,
) -> Motion:
    visual = signals.get("visual_yaw_rate_rad_s")
    props = {} if visual is None else visual.properties
    blocked = props.get("motion_blocked") is True
    impeded = props.get("motion_impeded") is True
    drive_s, predicted_yaw = _applied_motion(
        applications, trail["turn_response"], polarity_multiplier(trail["throttle_sign"]),
        trail["at_ms"], now,
    )
    expansion = signals.get("visual_expansion_rate_per_s")
    observed = None if expansion is None else expansion.properties.get("motion_direction")
    direction = {"forward": 1, "reverse": -1, "stationary": 0}.get(str(observed))
    drive_direction = (drive_s > 0.0) - (drive_s < 0.0)
    if interval_s > 0.0 and _sparse_tracks_are_stationary(visual):
        return Motion(0.0, 0.0, drive_direction, abs(drive_s), blocked, impeded, True, False)
    yaw = _visual_yaw(visual, interval_s)
    yaw_rad = 0.0 if blocked or impeded else predicted_yaw
    if yaw is not None:
        yaw_rad = yaw
    # Horizontal flow around a close obstacle can invert expansion during a
    # turn. Prefer the covered travel direction once its polarity is known.
    distance = drive_s
    if direction == 0 or (direction is not None and (not applications or trail["throttle_sign"] == "unknown")):
        distance = direction * interval_s
    if blocked:
        distance = 0.0
    elif impeded:
        distance *= 0.25
    stationary = direction == 0 and yaw is not None and abs(yaw) < 0.07
    return Motion(distance, yaw_rad, drive_direction or direction or 0,
                  abs(drive_s), blocked, impeded, stationary, unobserved=yaw is None and direction is None)


def _visit(trail: TrailPublication, pose: Pose, distance_s: float, now: int) -> dict[str, VisitedCell]:
    visited = trail["visited"]
    if distance_s == 0.0:
        return visited
    key = cell_key(pose["x_s"], pose["z_s"])
    previous = trail["pose"]
    entered = key != cell_key(previous["x_s"], previous["z_s"])
    cell = visited.get(key)
    weight = (0.0 if cell is None else decayed_weight(cell, now)) + abs(distance_s) / CELL_SIZE_S
    updated: VisitedCell = {
        "visits": 1 if cell is None else cell["visits"] + int(entered),
        "weight": weight + int(entered),
        "last_visited_at_ms": now,
    }
    newest = {**visited, key: updated}
    if len(newest) > CELL_LIMIT:
        oldest = min(visited, key=lambda index: visited[index]["last_visited_at_ms"])
        newest.pop(oldest)
    return newest


def _contacts(trail: TrailPublication, pose: Pose, motion: Motion, now: int) -> list[Contact]:
    recent = [contact for contact in trail["contacts"] if now - contact["at_ms"] <= CONTACT_MAX_AGE_MS]
    if not (motion.blocked or motion.impeded) or motion.drive_direction == 0:
        return recent
    contact: Contact = {**pose, "direction": motion.drive_direction, "at_ms": now}
    if recent:
        last = recent[-1]
        same_approach = (
            last["direction"] == contact["direction"] and
            math.cos(last["heading_rad"] - pose["heading_rad"]) > 0.94 and
            math.hypot(last["x_s"] - pose["x_s"], last["z_s"] - pose["z_s"]) < 0.22
        )
        if same_approach:
            recent.pop()
    return [*recent, contact][-CONTACT_LIMIT:]


def _sample_motion(
    state: TrailState, pose: Pose, yaw_rad: float, now: int,
) -> tuple[list[MotionSample], float, bool]:
    swept = state["swept_rad"] + abs(yaw_rad)
    recent = [sample for sample in state["motion"] if now - sample["at_ms"] <= LOOP_WINDOW_MS]
    loop = any(
        swept - sample["swept_rad"] >= 3.6
        and math.hypot(pose["x_s"] - sample["x_s"], pose["z_s"] - sample["z_s"]) < 0.50
        for sample in recent
    )
    if not recent or now - recent[-1]["at_ms"] >= MOTION_SAMPLE_MS:
        recent.append({**pose, "at_ms": now, "swept_rad": swept})
    return recent, swept, loop


def _start_escape(
    trail: TrailPublication, direction: int, last_side: int, cause: EscapeCause,
) -> Escape:
    pose, now = trail["pose"], trail["at_ms"]

    def turn_cost(side: int) -> float:
        probe = advance(advance(pose, -0.30 * direction, side * 1.0), 0.70 * direction, side * 0.6)
        return visit_density(trail["visited"], probe, now) + 2.0 * contact_pressure(
            trail["contacts"], probe, now, direction
        )

    other_side = -last_side
    turn_side = last_side if turn_cost(last_side) + 0.40 < turn_cost(other_side) else other_side
    return {
        "phase": "back_out", "direction": -direction, "turn_side": turn_side,
        "heading_at_contact": pose["heading_rad"], "phase_started_at_ms": now,
        "backing_s": 0.0, "cause": cause,
    }


def _back_out(escape: Escape, motion: Motion, now: int) -> Escape:
    backing = motion.drive_direction == escape["direction"]
    backing_s = escape["backing_s"] + (motion.drive_s if backing else 0.0)
    reverse_blocked = backing and (motion.blocked or motion.impeded)
    timed_out = now - escape["phase_started_at_ms"] >= BACK_OUT_TIMEOUT_MS
    if reverse_blocked or backing_s >= BACK_OUT_S or timed_out:
        return {**escape, "phase": "turn_away", "direction": -escape["direction"],
                "phase_started_at_ms": now, "backing_s": backing_s}
    return {**escape, "backing_s": backing_s}


def _escape_directive(
    trail: TrailPublication, escape: Escape | None, motion: Motion, last_side: int, cause: EscapeCause | None,
) -> Escape | None:
    contact = motion.blocked or motion.impeded
    if escape is None:
        return None if cause is None else _start_escape(trail, motion.drive_direction or 1, last_side, cause)

    if escape["phase"] == "back_out":
        return _back_out(escape, motion, trail["at_ms"])

    turned = abs(trail["pose"]["heading_rad"] - escape["heading_at_contact"])
    timed_out = trail["at_ms"] - escape["phase_started_at_ms"] >= TURN_AWAY_TIMEOUT_MS
    if turned >= TURN_AWAY_RAD or timed_out:
        return None
    if contact and escape["direction"] == motion.drive_direction:
        # A direction change keeps the same intended yaw if the other end hits a wall.
        return {**escape, "direction": -escape["direction"]}
    return escape


def _escape_cause(motion: Motion, loop: bool, unobserved_s: float) -> EscapeCause | None:
    if motion.drive_direction and (motion.blocked or motion.impeded):
        return "contact"
    if loop:
        return "loop"
    if unobserved_s >= UNOBSERVED_TRAVEL_LIMIT_S:
        return "tracking"
    return None


def _refresh_controls(
    trail: TrailPublication, signals: dict[str, PerceptionSignal], active: tuple[ControlApplication, ...],
) -> TrailPublication:
    expansion = signals.get("visual_expansion_rate_per_s")
    polarity = "unknown" if expansion is None else _polarity(expansion.properties.get("throttle_sign"))
    updated: TrailPublication = {
        **trail,
        "turn_response": _turn_response(signals.get("turn_response_available")) or trail["turn_response"],
        "throttle_sign": trail["throttle_sign"] if polarity == "unknown" else polarity,
    }
    if active:
        control = active[-1].control
        updated["applied_steering"] = control.steering
        updated["applied_direction"] = _direction(control.throttle) * polarity_multiplier(updated["throttle_sign"])
    return updated


def _update_trail(
    state: TrailState, signals: dict[str, PerceptionSignal], shared_memory: SharedMemory, now: int,
) -> TrailState:
    trail = state["publication"]
    elapsed_ms = now - trail["at_ms"]
    interval_s = elapsed_ms / 1000.0
    applications = control_applications_over(shared_memory, trail["at_ms"], now) or ()
    learned = _refresh_controls(trail, signals, applications or control_applications_over(shared_memory, now, now) or ())
    # The camera pair may abstain over a long gap even though the applied
    # controls still cover it end to end.
    motion = _motion(learned, signals if elapsed_ms <= MAX_INTERVAL_MS else {}, applications, interval_s, now)
    stationary_s = state["stationary_s"] + motion.drive_s if motion.stationary and motion.drive_s else 0.0
    unobserved_s = state["unobserved_s"] + motion.drive_s if motion.unobserved and motion.drive_s else 0.0
    if stationary_s >= STATIONARY_CONTACT_S:
        motion = replace(motion, blocked=True)
    pose = advance(trail["pose"], motion.distance_s, motion.yaw_rad)
    updated: TrailPublication = {
        **learned, "at_ms": now, "interval_s": interval_s, "pose": pose,
        "visited": _visit(trail, pose, motion.distance_s, now),
        "contacts": _contacts(trail, pose, motion, now),
    }
    recent, swept, loop = _sample_motion(state, pose, motion.yaw_rad, now)
    cause = _escape_cause(motion, loop, unobserved_s)
    escape = _escape_directive(updated, trail["escape"], motion, state["last_turn_side"], cause)
    updated["escape"] = escape
    if trail["escape"] is not None or escape is not None:
        recent, swept, unobserved_s = [], 0.0, 0.0
    if escape is not None and escape["direction"] != motion.drive_direction:
        stationary_s = 0.0
    return {
        "publication": updated, "motion": recent, "swept_rad": swept,
        "last_turn_side": state["last_turn_side"] if escape is None else escape["turn_side"],
        "stationary_s": stationary_s,
        "unobserved_s": unobserved_s,
    }
