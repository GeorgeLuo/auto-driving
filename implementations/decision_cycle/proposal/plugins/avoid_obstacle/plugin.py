"""After a collision, back up until the right is clear, then turn right until ahead is clear.

A collision is a static view while any configured obstacle signal (by default
a cardboard obstacle or a wall) is in sight. When the view stays static for
more than ``max_static_cycles`` while backing up, the car cannot reverse any
farther, so it turns right anyway; a turn that stalls backs up again. The
maneuver lives in shared memory so a fresh instance continues it. Outside the
maneuver the candidate is inactive.

The maneuver also starts before a collision once the lowest lower edge of a box
ahead (``clearance_ahead``'s ``nearest_bottom_y``) reaches the stop row that
memory's ``stop_row`` plugin has learned, published at ``stop_row_belief_id``.

Short of the stop row, when perception's closeness reports the nearest box
ahead ``near`` and a side band has more reach (the learned time until its
nearest box would reach the stop row; a band with nothing in it has unbounded
reach), the car turns toward the side with the most reach, right on a tie,
without backing up first. The turn lasts while ahead stays near and that side
still has more reach than ahead, so it ends facing the most open direction the
turn passed. It backs up if the box ahead reaches the stop row or the view goes
static. A reach turn is stored with its ``direction``; other turns are right.
"""

from __future__ import annotations

import math
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.publication import belief_key, read_belief
from autonomy.decision_cycle.observation.publication import read_observation
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.proposal.values import (
    COMMAND_EPS, ActionProposal, ProposedVehicleCommand, SourceRef,
)
from autonomy.shared_memory import SharedMemory

BACKING_UP = "backing_up"
TURNING = "turning"
NEAR = "near"
REACH = "reach"
LEFT = "left"
RIGHT = "right"
PHASE_SIGNAL_IDS = ("view_static", "clearance_right", "clearance_ahead")
ASSUMPTIONS = ("collision_is_static_view_with_obstacle_in_sight", "positive_steering_turns_right")


class AvoidObstacle:
    plugin_id = "avoid_obstacle"

    def __init__(
        self,
        *,
        throttle: float = 0.60,
        reverse_throttle: float = -0.60,
        turn_steering: float = 1.0,
        obstacle_signal_ids: tuple[str, ...] | list[str] = ("obstacle_in_sight", "wall_in_sight"),
        max_static_cycles: int = 8,
        stop_row_belief_id: str = "stop_row.row",
        closeness_ahead_id: str = "closeness_ahead",
        closeness_left_id: str = "closeness_left",
        closeness_right_id: str = "closeness_right",
    ) -> None:
        self.throttle = float(throttle)
        self.reverse_throttle = float(reverse_throttle)
        self.turn_steering = float(turn_steering)
        if not math.isfinite(self.throttle) or not COMMAND_EPS <= self.throttle <= 1:
            raise ValueError("throttle must be a finite normalized forward command")
        if not math.isfinite(self.reverse_throttle) or not -1 <= self.reverse_throttle <= -COMMAND_EPS:
            raise ValueError("reverse_throttle must be a finite normalized reverse command")
        if not math.isfinite(self.turn_steering) or not COMMAND_EPS <= self.turn_steering <= 1:
            raise ValueError("turn_steering must be a finite normalized rightward command")
        if (
            not isinstance(obstacle_signal_ids, (tuple, list))
            or not obstacle_signal_ids
            or not all(isinstance(signal_id, str) and signal_id for signal_id in obstacle_signal_ids)
        ):
            raise ValueError("obstacle_signal_ids must be a non-empty list of signal ids")
        if isinstance(max_static_cycles, bool) or not isinstance(max_static_cycles, int) or max_static_cycles < 1:
            raise ValueError("max_static_cycles must be a positive integer")
        for name, value in (
            ("stop_row_belief_id", stop_row_belief_id), ("closeness_ahead_id", closeness_ahead_id),
            ("closeness_left_id", closeness_left_id), ("closeness_right_id", closeness_right_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        self.obstacle_signal_ids = tuple(obstacle_signal_ids)
        self.max_static_cycles = max_static_cycles
        self.stop_row_belief_id = stop_row_belief_id
        self.closeness_ids = {"ahead": closeness_ahead_id, LEFT: closeness_left_id, RIGHT: closeness_right_id}

    @property
    def _maneuver_key(self) -> str:
        return f"proposal.{self.plugin_id}.maneuver"

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        if shared_memory is not None:
            shared_memory.pop(self._maneuver_key, None)

    def propose(self, context: DecisionFrameContext) -> ActionProposal:
        shared_memory = context.shared_memory
        if shared_memory is None:
            return self._inactive(context, "no_shared_memory")
        observation = read_observation(shared_memory)
        previous = shared_memory.get(self._maneuver_key)
        signals = _signals(observation, PHASE_SIGNAL_IDS + self.obstacle_signal_ids)
        belief = read_belief(shared_memory, self.stop_row_belief_id)
        stop_row = None if belief is None else _stop_row(belief.value)
        ahead_row = _ahead_row(observation)
        bands = _closeness(observation, self.closeness_ids)
        maneuver = self._next_maneuver(previous, signals, stop_row, ahead_row, bands)
        if maneuver is None:
            shared_memory.pop(self._maneuver_key, None)
            return self._inactive(context, "no_collision")
        shared_memory[self._maneuver_key] = maneuver
        phase = maneuver["phase"]
        if phase == BACKING_UP:
            reason = "avoid_back_up_until_right_clear"
            command = ProposedVehicleCommand(steering=0.0, throttle=self.reverse_throttle, gear="reverse")
        elif maneuver.get("cause") == REACH:
            direction = maneuver["direction"]
            reason = f"avoid_turn_{direction}_toward_reach"
            steering = self.turn_steering if direction == RIGHT else -self.turn_steering
            command = ProposedVehicleCommand(steering=steering, throttle=self.throttle, gear="forward")
        else:
            reason = "avoid_turn_right"
            command = ProposedVehicleCommand(steering=self.turn_steering, throttle=self.throttle, gear="forward")
        refs = [SourceRef(kind="memory_record", id=self._maneuver_key, frame_id=context.frame_id, note=phase)]
        if previous is None and phase == BACKING_UP and not self._collided(signals):
            refs.append(SourceRef(
                kind="memory_record", id=belief_key(self.stop_row_belief_id), frame_id=context.frame_id,
                note=f"stop_row {stop_row}",
            ))
        if observation is not None:
            refs.append(SourceRef(
                kind="observation", id=observation.observation_id, frame_id=context.frame_id,
                observation_id=observation.observation_id, note="clearance_and_collision_signals",
            ))
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=context.frame_id,
            lifecycle="fresh",
            freshness="fresh",
            confidence=1.0,
            reason=reason,
            command=command,
            assumptions=ASSUMPTIONS,
            source_refs=tuple(refs),
            available=True,
        )

    def _next_maneuver(
        self, maneuver: Any, signals: dict[str, Any], stop_row: float | None, ahead_row: float | None,
        bands: dict[str, tuple[Any, float | None]],
    ) -> dict[str, Any] | None:
        """One transition per cycle; a missing or unknown signal keeps the maneuver."""

        phase = maneuver.get("phase") if isinstance(maneuver, dict) else None
        static = signals.get("view_static")
        if phase == BACKING_UP:
            if signals.get("clearance_right") is True:
                return {"phase": TURNING}
            static_cycles = maneuver.get("static_cycles", 0)
            if static is True:
                static_cycles += 1
            elif static is False:
                static_cycles = 0
            if static_cycles > self.max_static_cycles:
                return {"phase": TURNING}
            return {"phase": BACKING_UP, "static_cycles": static_cycles}
        if phase == TURNING:
            if static is True:
                return {"phase": BACKING_UP, "static_cycles": 1}
            if maneuver.get("cause") == REACH:
                if stop_row is not None and ahead_row is not None and ahead_row >= stop_row:
                    return {"phase": BACKING_UP, "static_cycles": 0}
                if not bands:
                    return maneuver
                return maneuver if maneuver.get("direction") in _more_reach(bands) else None
            return None if signals.get("clearance_ahead") is True else maneuver
        if self._collided(signals):
            return {"phase": BACKING_UP, "static_cycles": 1}
        if stop_row is not None and ahead_row is not None and ahead_row >= stop_row:
            return {"phase": BACKING_UP, "static_cycles": 0}
        sides = _more_reach(bands)
        if sides:
            return {"phase": TURNING, "cause": REACH, "direction": sides[0]}
        return None

    def _collided(self, signals: dict[str, Any]) -> bool:
        return signals.get("view_static") is True and any(
            signals.get(signal_id) is True for signal_id in self.obstacle_signal_ids
        )

    def _inactive(self, context: DecisionFrameContext, reason: str) -> ActionProposal:
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=context.frame_id,
            lifecycle="inactive",
            freshness="none",
            confidence=0.0,
            reason=reason,
            command=None,
            assumptions=ASSUMPTIONS,
            source_refs=(),
            available=False,
        )


def _stop_row(row: Any) -> float | None:
    """The learned stop row, or None before the first collision."""

    return float(row) if _is_row(row) else None


def _ahead_row(observation: Observation | None) -> float | None:
    """The lowest lower edge of any box ahead, or None."""

    if observation is None:
        return None
    for signal in observation.signals:
        if signal.get("signal_id") == "clearance_ahead":
            row = (signal.get("properties") or {}).get("nearest_bottom_y")
            return float(row) if _is_row(row) else None
    return None


def _closeness(
    observation: Observation | None, signal_ids: dict[str, str],
) -> dict[str, tuple[Any, float | None]]:
    """Each band's closeness value and reach; nothing in the band is unbounded reach."""

    bands: dict[str, tuple[Any, float | None]] = {}
    if observation is None:
        return bands
    names = {signal_id: band for band, signal_id in signal_ids.items()}
    for signal in observation.signals:
        band = names.get(signal.get("signal_id"))
        if band is None or band in bands:
            continue
        properties = signal.get("properties") or {}
        reach = properties.get("reach_ms")
        if properties.get("reason") == "nothing_in_band":
            reach = math.inf
        bands[band] = (signal.get("value"), float(reach) if _is_reach(reach) else None)
    return bands


def _more_reach(bands: dict[str, tuple[Any, float | None]]) -> list[str]:
    """The sides with more reach than a near box ahead, most first, right on a tie."""

    value, ahead = bands.get("ahead", (None, None))
    if value != NEAR or ahead is None:
        return []
    sides = [(bands.get(side, (None, None))[1], side) for side in (RIGHT, LEFT)]
    more = [(reach, side) for reach, side in sides if reach is not None and reach > ahead]
    return [side for _reach, side in sorted(more, key=lambda pair: -pair[0])]


def _is_reach(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value >= 0 and not math.isnan(value)


def _is_row(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _signals(observation: Observation | None, signal_ids: tuple[str, ...]) -> dict[str, Any]:
    """The first value of each of ``signal_ids``, from the current observation."""

    values: dict[str, Any] = {}
    if observation is None:
        return values
    for signal in observation.signals:
        if signal.get("signal_id") in signal_ids:
            values.setdefault(signal["signal_id"], signal.get("value"))
    return values
