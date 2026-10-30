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

Applied forward and straight reverse controls identify approaches and stops.
A turn ends a stop without admitting rows swept into view by steering. Unknown
motion drops a pending test. Accepted controls describe the target, not measured
wheel motion; collision and overshoot still come from the observation.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.beliefs import Belief, Check, Trial
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.memory.publication import belief_key, read_belief
from autonomy.decision_cycle.memory.interface import LEDGER_HEALTH_EMPTY, LEDGER_HEALTH_HEALTHY
from autonomy.decision_cycle.observation.publication import read_observation
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory
from implementations.decision_cycle.memory.shared.motion.control_applications import latest_motion, motion_at, motions_over

BELIEF_ID = "stop_row.row"
STATE_KEY = "memory.stop_row.hypothesis"
COLLISION = "collision"
STOPPED_SHORT = "stopped_short"
TOO_CLOSE = "too_close"
OUTCOMES = (COLLISION, STOPPED_SHORT, TOO_CLOSE)


class StopRowHypothesis:
    plugin_id = "stop_row"
    contract = MemoryPluginContract(
        description="Bisect the stop row using observed overshoot after applied reverse controls.",
        assumptions=("positive throttle drives forward", "straight reverse brakes a forward approach"),
        belief_ids=(BELIEF_ID,),
        limitations=("accepted controls are not measured wheel motion", "unknown motion retires a test"),
    )

    def __init__(
        self,
        *,
        min_stop_row: float = 0.6,
        blind_row: float = 0.98,
        resolution: float = 0.02,
        settle_ms: int = 1000,
        obstacle_signal_ids: tuple[str, ...] | list[str] = ("obstacle_in_sight", "wall_in_sight"),
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
        self.min_stop_row = float(min_stop_row)
        self.blind_row = float(blind_row)
        self.resolution = float(resolution)
        self.settle_ms = settle_ms
        self.obstacle_signal_ids = tuple(obstacle_signal_ids)

    def update(self, context: DecisionFrameContext) -> None:
        shared_memory = context.shared_memory
        if shared_memory is None:
            raise ValueError("stop_row requires a shared-memory map")
        observation = read_observation(shared_memory)
        belief = read_belief(shared_memory, BELIEF_ID)
        state = self._state(shared_memory.get(STATE_KEY))
        state["stop_row"] = None if belief is None else belief.value
        prediction = None if belief is None or not belief.trials else belief.trials[0]
        state["trial"] = None if prediction is None else {
            "stop_row": prediction.predicted, "started_at_ms": prediction.started_at_ms, **prediction.properties,
        }
        now = context.timestamp_ms
        captured_at = _captured_at(observation, now)
        if observation is not None and (
            observation.observation_id == state.get("observation_id")
            or captured_at == state.get("captured_at_ms")
        ):
            return  # Replayed camera frames cannot test a hypothesis twice.
        signals = _signals(observation)
        row = _ahead_row(signals)
        identity = _ahead_identity(observation, signals, row)
        motion = motion_at(shared_memory, captured_at)
        issued = latest_motion(shared_memory)
        previous = state["updated_at_ms"]
        previous_capture = state.get("captured_at_ms")
        span = None if previous_capture is None else motions_over(shared_memory, previous_capture, captured_at)
        if previous is not None and (span is None or now < previous):
            state["trial"] = None
            motion = issued = None
            if belief is not None:
                belief = replace(belief, trials=())
        forward = motion == "forward"
        static = _value(signals, "view_static")
        collided = forward and static is True and any(
            _value(signals, signal_id) is True for signal_id in self.obstacle_signal_ids
        )
        tracked = identity is not None and identity == state.get("ahead_identity") and span == ("forward",)
        trial = state["trial"]
        collision_check = None
        concluded = False
        if collided and state["stop_row"] is not None and tracked:
            predicted = state["stop_row"] if trial is None else trial["stop_row"]
            self._conclude(state, COLLISION, predicted, row, now)
            collision_check = Check(now, "collision", "contradict", predicted,
                                    {"obstacle": identity, "row": row})
            concluded = True
        elif trial is not None:
            # Captures may still show the forward approach after reverse was issued.
            if motion in ("forward", "reverse") and row is not None:
                trial["peak_row"] = max(trial["peak_row"], row)
            if identity != trial.get("obstacle") or static is not False:
                trial["obstacle"] = None
            if issued == "reverse":
                trial["reversed"] = True
            if issued in (None, "forward", "idle") or not trial.get("reversed"):
                state["trial"] = None
            elif trial["peak_row"] >= self.blind_row:
                self._conclude(state, TOO_CLOSE, trial["stop_row"], trial["peak_row"], now)
                concluded = True
            elif issued != "reverse" or now - trial["started_at_ms"] >= self.settle_ms:
                self._conclude(state, STOPPED_SHORT, trial["stop_row"], trial["peak_row"], now)
                concluded = True
                if trial.get("obstacle") is not None:
                    collision_check = Check(now, "collision", "support", trial["stop_row"],
                                            {"obstacle": trial["obstacle"], "contact": False})
        elif collided:
            state["counts"][COLLISION] += 1
            state["last_outcome"] = _outcome(COLLISION, state["stop_row"], row, now)
            if state["stop_row"] is None:
                state["stop_row"] = self._next_stop_row(state)
        elif (forward and issued == "forward" and state["stop_row"] is not None
              and row is not None and row >= state["stop_row"]):
            state["trial"] = {"stop_row": state["stop_row"], "started_at_ms": now, "peak_row": row,
                              "obstacle": identity if tracked and static is False else None}
        if state["stop_row"] is not None:
            if belief is None:
                belief = Belief(BELIEF_ID, state["stop_row"], self.plugin_id, now)
            if concluded:
                outcome = state["last_outcome"]
                belief = belief.revised(state["stop_row"], now, keep_checks=not state.pop("invalidated", False))
                if belief.trials:
                    belief = belief.resolved(belief.trials[0].trial_id,
                        "support" if outcome["kind"] == STOPPED_SHORT else "contradict",
                        outcome, now, 64)
                if collision_check is not None:
                    belief = belief.checked(collision_check, 64)
            trial = state["trial"]
            if trial is None:
                belief = replace(belief, trials=())
            else:
                properties = {key: trial[key] for key in ("peak_row", "reversed", "obstacle") if key in trial}
                if not belief.trials:
                    belief = belief.opened(Trial(f"stop_{now}", "stop", now, trial["stop_row"], properties), 1)
                else:
                    belief = replace(belief, trials=(replace(belief.trials[0], properties=properties),))
            shared_memory[belief_key(BELIEF_ID)] = belief
        state["ahead_identity"] = identity if forward and static is False else None
        state["observation_id"] = None if observation is None else observation.observation_id
        state["captured_at_ms"] = captured_at
        state["updated_at_ms"] = now
        state.pop("stop_row")
        state.pop("trial")
        shared_memory[STATE_KEY] = state

    def status(self, shared_memory: SharedMemory | None) -> dict[str, Any]:
        stored = None if shared_memory is None else shared_memory.get(STATE_KEY)
        state = self._state(stored)
        belief = read_belief(shared_memory, BELIEF_ID)
        state["stop_row"] = None if belief is None else belief.value
        return {
            "health": LEDGER_HEALTH_EMPTY if stored is None else LEDGER_HEALTH_HEALTHY,
            "record_count": sum(state["counts"].values()),
            "stop_row": state["stop_row"],
            "safe_row": state["safe_row"],
            "unsafe_row": state["unsafe_row"],
            "testing": belief is not None and bool(belief.trials),
            "counts": dict(state["counts"]),
            "last_outcome": state["last_outcome"],
        }

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        if shared_memory is not None:
            shared_memory.pop(STATE_KEY, None)
            shared_memory.pop(belief_key(BELIEF_ID), None)

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
                state["invalidated"] = True
        else:  # TOO_CLOSE
            state["unsafe_row"] = max(self.min_stop_row, stop_row)
            if state["safe_row"] >= state["unsafe_row"]:
                state["safe_row"] = self.min_stop_row
                state["invalidated"] = True
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


def _captured_at(observation: Observation | None, fallback: int) -> int:
    readings = None if observation is None else observation.sensor_frame.get("readings")
    reading = readings.get("front_camera") if isinstance(readings, dict) else None
    at = reading.get("captured_at_ms") if isinstance(reading, dict) else None
    return at if isinstance(at, int) and not isinstance(at, bool) and at >= 0 else fallback


def _ahead_identity(
    observation: Observation | None, signals: dict[str, dict[str, Any]], row: float | None,
) -> str | None:
    """An unambiguous tracked box at clearance's nearest row, never a detection index."""
    if observation is None or row is None:
        return None
    properties = signals.get("clearance_ahead", {}).get("properties") or {}
    min_x, max_x = properties.get("min_x", 0.3), properties.get("max_x", 0.7)
    identities = []
    boxes = []
    for thing in observation.things:
        if not isinstance(thing, dict) or thing.get("kind") != "obstruction_evidence":
            continue
        bbox = (thing.get("location") or {}).get("bbox_xyxy_norm")
        object_id = (thing.get("properties") or {}).get("object_id")
        source = thing.get("source_plugin_id")
        if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
                or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                           and math.isfinite(value) for value in bbox)):
            continue
        if bbox[0] < max_x and bbox[2] > min_x and math.isclose(bbox[3], row, rel_tol=0, abs_tol=1e-9):
            boxes.append(tuple(bbox))
            if isinstance(object_id, int) and not isinstance(object_id, bool) and isinstance(source, str):
                identities.append((f"{source}#{object_id}", tuple(bbox)))
    # The detector and its tracker can publish the same box. A different box at
    # the same row makes contact attribution ambiguous, even if it has no ID.
    if len(identities) != 1 or any(box != identities[0][1] for box in boxes):
        return None
    return identities[0][0]
