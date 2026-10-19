"""Learn how near an obstacle ahead may come before the car starts avoiding it.

The hypothesis is a stop row: start avoiding once the lowest lower edge of a box
ahead (perception's ``clearance_ahead`` ``nearest_bottom_y``) reaches it. The
first collision while driving forward starts it. Every stop then tests it: the
car keeps rolling briefly after it starts reversing, so the lower edge keeps
descending. One that reaches ``blind_row`` has left the image, so the car came
too close; one that stays above it until the stop ends stopped short. The stop
row is bisected between the deepest row that stopped short and the shallowest
that came too close. A row that fails after passing, or passes after failing,
discards the older evidence.

The car is maneuvering while ``maneuver_key`` holds a value; the stop is its
``stop_phase``. A test measures only that phase and ends when the maneuver
moves on (a turn sweeps other boxes into the ahead band) or after
``settle_ms``. A test with no maneuver stored after its first cycle tested
nothing and is dropped. Memory runs before proposal, so a static view with an
obstacle in sight while no maneuver is stored is a collision while driving
forward. A head-on approach crosses the stop row and starts a test well before
the view is static, so a later collision was not head-on: it is counted but
does not move the stop row.
"""

from __future__ import annotations

import math
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.interface import LEDGER_HEALTH_EMPTY, LEDGER_HEALTH_HEALTHY
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory

STATE_KEY = "memory.stop_row.hypothesis"
COLLISION = "collision"
STOPPED_SHORT = "stopped_short"
TOO_CLOSE = "too_close"
OUTCOMES = (COLLISION, STOPPED_SHORT, TOO_CLOSE)


class StopRowHypothesis:
    plugin_id = "stop_row"

    def __init__(
        self,
        *,
        min_stop_row: float = 0.6,
        blind_row: float = 0.98,
        resolution: float = 0.02,
        settle_ms: int = 1000,
        obstacle_signal_ids: tuple[str, ...] | list[str] = ("obstacle_in_sight", "wall_in_sight"),
        maneuver_key: str = "proposal.avoid_obstacle.maneuver",
        stop_phase: str = "backing_up",
    ) -> None:
        for name, value in (("min_stop_row", min_stop_row), ("blind_row", blind_row), ("resolution", resolution)):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 < value <= 1
            ):
                raise ValueError(f"{name} must be a finite number in (0, 1]")
        if not min_stop_row < blind_row:
            raise ValueError("min_stop_row must be below blind_row")
        if isinstance(settle_ms, bool) or not isinstance(settle_ms, int) or settle_ms < 1:
            raise ValueError("settle_ms must be a positive integer")
        if (
            not isinstance(obstacle_signal_ids, (tuple, list))
            or not obstacle_signal_ids
            or not all(isinstance(signal_id, str) and signal_id for signal_id in obstacle_signal_ids)
        ):
            raise ValueError("obstacle_signal_ids must be a non-empty list of signal ids")
        for name, value in (("maneuver_key", maneuver_key), ("stop_phase", stop_phase)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        self.min_stop_row = float(min_stop_row)
        self.blind_row = float(blind_row)
        self.resolution = float(resolution)
        self.settle_ms = settle_ms
        self.obstacle_signal_ids = tuple(obstacle_signal_ids)
        self.maneuver_key = maneuver_key
        self.stop_phase = stop_phase

    def update(self, context: DecisionFrameContext, observation: Observation | None) -> None:
        shared_memory = context.shared_memory
        if shared_memory is None:
            raise ValueError("stop_row requires a shared-memory map")
        state = self._state(shared_memory.get(STATE_KEY))
        signals = _signals(observation)
        row = _ahead_row(signals)
        now = context.timestamp_ms
        maneuver = shared_memory.get(self.maneuver_key)
        maneuvering = maneuver is not None
        trial = state["trial"]
        if trial is not None:
            stopping = isinstance(maneuver, dict) and maneuver.get("phase") == self.stop_phase
            if stopping and row is not None:
                trial["peak_row"] = max(trial["peak_row"], row)
            if not maneuvering:
                state["trial"] = None
            elif trial["peak_row"] >= self.blind_row:
                self._conclude(state, TOO_CLOSE, trial["stop_row"], trial["peak_row"], now)
            elif not stopping or now - trial["started_at_ms"] >= self.settle_ms:
                self._conclude(state, STOPPED_SHORT, trial["stop_row"], trial["peak_row"], now)
        elif not maneuvering and _value(signals, "view_static") is True and any(
            _value(signals, signal_id) is True for signal_id in self.obstacle_signal_ids
        ):
            state["counts"][COLLISION] += 1
            state["last_outcome"] = _outcome(COLLISION, state["stop_row"], row, now)
            if state["stop_row"] is None:
                state["stop_row"] = self._next_stop_row(state)
        elif not maneuvering and state["stop_row"] is not None and row is not None and row >= state["stop_row"]:
            state["trial"] = {"stop_row": state["stop_row"], "started_at_ms": now, "peak_row": row}
        state["updated_at_ms"] = now
        shared_memory[STATE_KEY] = state

    def status(self, shared_memory: SharedMemory | None) -> dict[str, Any]:
        stored = None if shared_memory is None else shared_memory.get(STATE_KEY)
        state = self._state(stored)
        return {
            "health": LEDGER_HEALTH_EMPTY if stored is None else LEDGER_HEALTH_HEALTHY,
            "record_count": sum(state["counts"].values()),
            "stop_row": state["stop_row"],
            "safe_row": state["safe_row"],
            "unsafe_row": state["unsafe_row"],
            "testing": state["trial"] is not None,
            "counts": dict(state["counts"]),
            "last_outcome": state["last_outcome"],
        }

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        if shared_memory is not None:
            shared_memory.pop(STATE_KEY, None)

    def _state(self, stored: Any) -> dict[str, Any]:
        """A copy of the stored hypothesis, or a fresh one with no stop row yet."""

        if isinstance(stored, dict) and isinstance(stored.get("counts"), dict):
            state = dict(stored)
            state["counts"] = {outcome: int(stored["counts"].get(outcome, 0)) for outcome in OUTCOMES}
            state["trial"] = dict(stored["trial"]) if isinstance(stored.get("trial"), dict) else None
            return state
        return {
            "stop_row": None,
            "safe_row": self.min_stop_row,
            "unsafe_row": self.blind_row,
            "trial": None,
            "counts": {outcome: 0 for outcome in OUTCOMES},
            "last_outcome": None,
            "updated_at_ms": None,
        }

    def _conclude(self, state: dict[str, Any], kind: str, stop_row: float, peak_row: float | None, now: int) -> None:
        """Record one test of ``stop_row`` and move to the next hypothesis."""

        if kind == STOPPED_SHORT:
            state["safe_row"] = max(state["safe_row"], stop_row)
            if state["unsafe_row"] <= state["safe_row"]:
                state["unsafe_row"] = self.blind_row
        else:  # TOO_CLOSE
            state["unsafe_row"] = max(self.min_stop_row, stop_row)
            if state["safe_row"] >= state["unsafe_row"]:
                state["safe_row"] = self.min_stop_row
        state["counts"][kind] += 1
        state["last_outcome"] = _outcome(kind, stop_row, peak_row, now)
        state["trial"] = None
        state["stop_row"] = self._next_stop_row(state)

    def _next_stop_row(self, state: dict[str, Any]) -> float:
        """Halve the bracket until it is narrower than ``resolution``, then keep the safe row."""

        safe, unsafe = state["safe_row"], state["unsafe_row"]
        return round((safe + unsafe) / 2, 4) if unsafe - safe > self.resolution else safe


def _outcome(kind: str, stop_row: float | None, peak_row: float | None, now: int) -> dict[str, Any]:
    return {"kind": kind, "stop_row": stop_row, "peak_row": peak_row, "at_ms": now}


def _signals(observation: Observation | None) -> dict[str, dict[str, Any]]:
    """This observation's signals by id; the first of each id wins."""

    signals: dict[str, dict[str, Any]] = {}
    for signal in () if observation is None else observation.signals:
        if isinstance(signal, dict) and isinstance(signal.get("signal_id"), str):
            signals.setdefault(signal["signal_id"], signal)
    return signals


def _value(signals: dict[str, dict[str, Any]], signal_id: str) -> Any:
    return signals.get(signal_id, {}).get("value")


def _ahead_row(signals: dict[str, dict[str, Any]]) -> float | None:
    """The lowest lower edge of any box ahead, in 0..1, or None."""

    properties = signals.get("clearance_ahead", {}).get("properties") or {}
    row = properties.get("nearest_bottom_y")
    if isinstance(row, bool) or not isinstance(row, (int, float)) or not math.isfinite(row):
        return None
    return float(row)
