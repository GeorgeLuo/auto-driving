"""The JSON values published by the trail and their action-time geometry."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Literal, TypedDict, cast

from autonomy.shared_memory import SharedMemory
from implementations.decision_cycle.perception.shared.motion.visual_yaw import predict_yaw_rate

TRAIL_KEY = "memory.relativistic_trail"
CELL_SIZE_S = 0.30
VISIT_HALF_LIFE_MS = 25_000
CONTACT_MAX_AGE_MS = 16_000

Polarity = Literal["normal", "inverted", "unknown"]
EscapeCause = Literal["contact", "loop", "tracking"]


class Pose(TypedDict):
    x_s: float
    z_s: float
    heading_rad: float


class VisitedCell(TypedDict):
    visits: int
    weight: float
    last_visited_at_ms: int


class Contact(Pose):
    direction: int
    at_ms: int


class TurnResponse(TypedDict):
    kind: Literal["linear", "ackermann_tan"]
    gain_per_s: float
    max_angle_rad: float
    turn_sign: Polarity


class Escape(TypedDict):
    phase: Literal["back_out", "turn_away"]
    direction: int
    turn_side: int
    heading_at_contact: float
    phase_started_at_ms: int
    backing_s: float
    cause: EscapeCause


class TrailPublication(TypedDict):
    at_ms: int
    interval_s: float
    pose: Pose
    visited: dict[str, VisitedCell]
    contacts: list[Contact]
    turn_response: TurnResponse | None
    throttle_sign: Polarity
    applied_steering: float | None
    applied_direction: int
    escape: Escape | None


def read_relativistic_trail(shared_memory: SharedMemory | None) -> TrailPublication | None:
    """Return a detached publication, or None before the plugin first runs."""
    value = None if shared_memory is None else shared_memory.get(TRAIL_KEY)
    return deepcopy(cast(TrailPublication, value)) if isinstance(value, dict) else None


def polarity_multiplier(sign: Polarity) -> int:
    return -1 if sign == "inverted" else 1


def steering_multiplier(response: TurnResponse | None, throttle_sign: Polarity) -> int:
    # The learned turn sign is relative to commanded steer times commanded throttle.
    if response is None:
        return 1
    return polarity_multiplier(response["turn_sign"]) * polarity_multiplier(throttle_sign)


def yaw_rate(response: TurnResponse | None, steering: float, throttle: float) -> float:
    if response is None:
        return 2.0 * steering * throttle
    return predict_yaw_rate(response["kind"], response["gain_per_s"], response["max_angle_rad"],
                            steering, throttle)


def advance(pose: Pose, distance_s: float, yaw_rad: float) -> Pose:
    midpoint = pose["heading_rad"] + yaw_rad / 2.0
    return {
        "x_s": pose["x_s"] + math.sin(midpoint) * distance_s,
        "z_s": pose["z_s"] + math.cos(midpoint) * distance_s,
        "heading_rad": pose["heading_rad"] + yaw_rad,
    }


def cell_key(x_s: float, z_s: float) -> str:
    return f"{math.floor(x_s / CELL_SIZE_S + 0.5)},{math.floor(z_s / CELL_SIZE_S + 0.5)}"


def decayed_weight(cell: VisitedCell, now: int) -> float:
    return cell["weight"] * 0.5 ** (max(0, now - cell["last_visited_at_ms"]) / VISIT_HALF_LIFE_MS)


def visit_density(visited: dict[str, VisitedCell], pose: Pose, now: int) -> float:
    # Interpolate neighboring cells so crossing a grid edge cannot flip a score.
    x, z = pose["x_s"] / CELL_SIZE_S, pose["z_s"] / CELL_SIZE_S
    ix, iz = math.floor(x), math.floor(z)
    density = 0.0
    for col in (ix, ix + 1):
        for row in (iz, iz + 1):
            cell = visited.get(f"{col},{row}")
            if cell is not None:
                density += decayed_weight(cell, now) * (1.0 - abs(x - col)) * (1.0 - abs(z - row))
    return density


def contact_pressure(contacts: list[Contact], pose: Pose, now: int, direction: int = 1) -> float:
    """Penalize driving back toward a remembered contact along its approach."""
    pressure = 0.0
    for contact in contacts:
        age_weight = max(0.0, 1.0 - (now - contact["at_ms"]) / CONTACT_MAX_AGE_MS)
        distance = math.hypot(pose["x_s"] - contact["x_s"], pose["z_s"] - contact["z_s"])
        alignment = direction * contact["direction"] * math.cos(pose["heading_rad"] - contact["heading_rad"])
        pressure = max(pressure, age_weight * max(0.0, 1.0 - distance / 0.8) * (0.3 + max(0.0, alignment)))
    return pressure
