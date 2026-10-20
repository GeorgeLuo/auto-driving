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
"""

from __future__ import annotations

import math
from typing import Any

from autonomy.decision_cycle.memory.publication import belief_key, read_belief
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.proposal.values import (
    COMMAND_EPS, ActionProposal, ProposedVehicleCommand, SourceRef,
)
from autonomy.shared_memory import SharedMemory

BACKING_UP = "backing_up"
TURNING = "turning"
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
        if not isinstance(stop_row_belief_id, str) or not stop_row_belief_id:
            raise ValueError("stop_row_belief_id must be a non-empty string")
        self.obstacle_signal_ids = tuple(obstacle_signal_ids)
        self.max_static_cycles = max_static_cycles
        self.stop_row_belief_id = stop_row_belief_id

    @property
    def _maneuver_key(self) -> str:
        return f"proposal.{self.plugin_id}.maneuver"

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        if shared_memory is not None:
            shared_memory.pop(self._maneuver_key, None)

    def propose(self, source: DecisionDataSource, shared_memory: SharedMemory | None) -> ActionProposal:
        if shared_memory is None:
            return self._inactive(source, "no_shared_memory")
        previous = shared_memory.get(self._maneuver_key)
        signals = _signals(source, PHASE_SIGNAL_IDS + self.obstacle_signal_ids)
        belief = read_belief(shared_memory, self.stop_row_belief_id)
        stop_row = None if belief is None else _stop_row(belief.value)
        maneuver = self._next_maneuver(previous, signals, stop_row, _ahead_row(source))
        if maneuver is None:
            shared_memory.pop(self._maneuver_key, None)
            return self._inactive(source, "no_collision")
        shared_memory[self._maneuver_key] = maneuver
        phase = maneuver["phase"]
        if phase == BACKING_UP:
            reason = "avoid_back_up_until_right_clear"
            command = ProposedVehicleCommand(steering=0.0, throttle=self.reverse_throttle, gear="reverse")
        else:
            reason = "avoid_turn_right"
            command = ProposedVehicleCommand(steering=self.turn_steering, throttle=self.throttle, gear="forward")
        refs = [SourceRef(kind="memory_record", id=self._maneuver_key, frame_id=source.frame_id, note=phase)]
        if previous is None and not self._collided(signals):
            refs.append(SourceRef(
                kind="memory_record", id=belief_key(self.stop_row_belief_id), frame_id=source.frame_id,
                note=f"stop_row {stop_row}",
            ))
        if source.observation.status == "ready":
            observation_id = source.observation.value.observation_id
            refs.append(SourceRef(
                kind="observation", id=observation_id, frame_id=source.frame_id,
                observation_id=observation_id, note="clearance_and_collision_signals",
            ))
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=source.frame_id,
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
            return None if signals.get("clearance_ahead") is True else maneuver
        if self._collided(signals):
            return {"phase": BACKING_UP, "static_cycles": 1}
        if stop_row is not None and ahead_row is not None and ahead_row >= stop_row:
            return {"phase": BACKING_UP, "static_cycles": 0}
        return None

    def _collided(self, signals: dict[str, Any]) -> bool:
        return signals.get("view_static") is True and any(
            signals.get(signal_id) is True for signal_id in self.obstacle_signal_ids
        )

    def _inactive(self, source: DecisionDataSource, reason: str) -> ActionProposal:
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=source.frame_id,
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


def _ahead_row(source: DecisionDataSource) -> float | None:
    """The lowest lower edge of any box ahead, or None."""

    if source.observation.status != "ready":
        return None
    for signal in source.observation.value.signals:
        if signal.get("signal_id") == "clearance_ahead":
            row = (signal.get("properties") or {}).get("nearest_bottom_y")
            return float(row) if _is_row(row) else None
    return None


def _is_row(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _signals(source: DecisionDataSource, signal_ids: tuple[str, ...]) -> dict[str, Any]:
    """The first value of each of ``signal_ids``, from a ready observation."""

    values: dict[str, Any] = {}
    if source.observation.status != "ready":
        return values
    for signal in source.observation.value.signals:
        if signal.get("signal_id") in signal_ids:
            values.setdefault(signal["signal_id"], signal.get("value"))
    return values
