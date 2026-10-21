"""Learn how the car's own turn command moves what it sees, and where landmarks sit.

Everything is in the car's own units, image widths and milliseconds: no camera
model, no heading in degrees and no map. A landmark is a thing that
``landmark_plugin_id`` tracks with an integer ``object_id`` property and a box.
Its bearing is the box centre's offset from the middle of the image, in image
widths, positive to the right. A box within ``edge_margin`` of either side is
clipped, so its centre is not the landmark's and it counts as unseen. A box
more than ``max_size_change`` times wider or narrower than the landmark's last
sighting is not that landmark (detectors split and merge boxes at the image
edge, and identities follow the pieces), so it starts a new one.

Each new camera frame (``sensor_id``'s capture time) closes one interval.
Applied controls covering that capture interval name it: ``turning`` if
all turn right forward, ``straight`` if all drive forward without steering,
and ``turn_start`` or ``turn_end`` if steering begins or ends within it.
A left turn is not learned: that interval places every landmark afresh.
Reverse-right, straight reverse, and idle learn as their own kinds. Missing
coverage or mixed directions restart tracking rather than attributing that
movement to a command. Its
sample is the median speed, in widths per second, of the landmarks seen at
both ends. Each kind keeps its last ``max_samples`` samples and its learned
speed is their median, so ordinary driving teaches the car how fast its turn
moves the view, how quickly a turn starts and stops, and whether driving
straight drifts.

A ``turning`` sample slower than ``stall_fraction`` of the learned turning
speeds' upper quartile is not learned from, and ``stall_intervals`` of them in
a row are a stall: the command turned and the view did not. Until a quarter of
the learned samples move, nothing is slow, so a car whose first turns stall
still learns its free turn once it has one.

Landmarks keep a bearing after they leave the view. Each interval moves an
unseen landmark by the speed measured from the landmarks still in view, or by
its kind's learned speed when none are. The model knows only rotation, so an
unseen landmark is forgotten after ``forget_straight_ms`` of straight driving
or when no speed is known for the interval, and every landmark is placed
afresh after an interval longer than ``max_interval_ms``. A landmark seen
again checks its carried bearing: a new identity of the same size within
``reacquire_within`` of a carried landmark takes that landmark's place, and the
gap between the carried and the seen bearing is the position error. At most
``max_landmarks`` are kept, the most recently seen.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.beliefs import Belief, Trial
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.memory.publication import belief_key, read_belief
from autonomy.decision_cycle.memory.interface import LEDGER_HEALTH_EMPTY, LEDGER_HEALTH_HEALTHY
from autonomy.decision_cycle.observation.publication import read_observation
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID
from implementations.decision_cycle.memory.shared.motion.control_applications import motions_over

STATE_KEY = "memory.turn_model.state"
TURN_START = "turn_start"
TURNING = "turning"
TURN_END = "turn_end"
STRAIGHT = "forward"
TURN_MOTIONS = ("forward_right", "reverse_right")
INTERVALS = ("forward", "reverse", "idle", *(
    f"{motion}.{phase}" for motion in TURN_MOTIONS for phase in (TURN_START, TURNING, TURN_END)
))
_FALLBACK = {
    f"{motion}.{phase}": f"{motion}.{TURNING}" if phase == TURN_START else motion.split("_")[0]
    for motion in TURN_MOTIONS for phase in (TURN_START, TURN_END)
}


def belief_id(interval):
    return f"turn_model.view_speed.{interval}"
_LANDMARK_FIELDS = ("bearing", "width", "in_view", "seen_at_ms", "carried_ms", "straight_ms")


class TurnModel:
    plugin_id = "turn_model"
    contract = MemoryPluginContract(
        description="Learn applied-command view speeds and check carried landmarks on re-sighting.",
        assumptions=("positive steering is right", "landmark identities and widths remain comparable",
                     "each motion kind uses comparable steering and throttle magnitudes"),
        belief_ids=tuple(belief_id(interval) for interval in INTERVALS),
        limitations=("accepted target writes are not measured wheel motion",
                     "checks require a single interval kind throughout the unseen span"),
    )

    def __init__(
        self,
        *,
        landmark_plugin_id: str = "sim_cardboard_permanence",
        sensor_id: str = FRONT_CAMERA_SENSOR_ID,
        max_samples: int = 64,
        max_interval_ms: int = 250,
        edge_margin: float = 0.01,
        max_size_change: float = 1.5,
        stall_fraction: float = 0.25,
        stall_intervals: int = 2,
        forget_straight_ms: int = 2000,
        reacquire_within: float = 0.15,
        max_landmarks: int = 16,
    ) -> None:
        for name, value in (
            ("landmark_plugin_id", landmark_plugin_id), ("sensor_id", sensor_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        for name, value in (
            ("max_samples", max_samples), ("max_interval_ms", max_interval_ms),
            ("stall_intervals", stall_intervals), ("forget_straight_ms", forget_straight_ms),
            ("max_landmarks", max_landmarks),
        ):
            if not _is_int(value) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name, value, upper in (
            ("edge_margin", edge_margin, 0.5), ("stall_fraction", stall_fraction, 1.0),
            ("reacquire_within", reacquire_within, 1.0),
        ):
            if not _is_number(value) or not 0 <= value < upper:
                raise ValueError(f"{name} must be a finite number in [0, {upper})")
        if not _is_number(max_size_change) or max_size_change < 1:
            raise ValueError("max_size_change must be a finite number of at least 1")
        self.landmark_plugin_id = landmark_plugin_id
        self.sensor_id = sensor_id
        self.max_samples = max_samples
        self.max_interval_ms = max_interval_ms
        self.edge_margin = float(edge_margin)
        self.max_size_change = float(max_size_change)
        self.stall_fraction = float(stall_fraction)
        self.stall_intervals = stall_intervals
        self.forget_straight_ms = forget_straight_ms
        self.reacquire_within = float(reacquire_within)
        self.max_landmarks = max_landmarks

    def update(self, context: DecisionFrameContext) -> None:
        shared_memory = context.shared_memory
        if shared_memory is None:
            raise ValueError("turn_model requires a shared-memory map")
        observation = read_observation(shared_memory)
        state = _restore(shared_memory.get(STATE_KEY))
        captured_at = _captured_at(observation, self.sensor_id)
        previous_at = state["captured_at_ms"]
        if captured_at is not None and previous_at is not None and captured_at < previous_at:
            _forget(state, "clock_step")
            previous_at = None
        if captured_at is not None and (previous_at is None or captured_at > previous_at):
            seen = self._seen(observation)
            motions = None if previous_at is None else motions_over(shared_memory, previous_at, captured_at)
            interval = _interval(motions)
            if interval != state["last_interval"]:
                state["slow_intervals"] = 0
            state["last_interval"] = interval
            elapsed = None if previous_at is None else captured_at - previous_at
            if elapsed is not None and elapsed <= self.max_interval_ms and interval is not None:
                speed = self._measure(state, interval, seen, elapsed, captured_at)
                self._carry(state, interval, seen, elapsed, speed, shared_memory, captured_at)
            else:
                _forget(state, "capture_gap" if elapsed is None or elapsed > self.max_interval_ms
                        else "unnamed_interval")
            self._observe(state, seen, captured_at, shared_memory)
            state["captured_at_ms"] = captured_at
        self._publish(shared_memory, state, context.timestamp_ms)
        state["updated_at_ms"] = context.timestamp_ms
        shared_memory[STATE_KEY] = state

    def status(self, shared_memory: SharedMemory | None) -> dict[str, Any]:
        stored = None if shared_memory is None else shared_memory.get(STATE_KEY)
        state = _restore(stored)
        errors = state["position_errors"]
        return {
            "health": LEDGER_HEALTH_EMPTY if stored is None else LEDGER_HEALTH_HEALTHY,
            "record_count": sum(len(samples) for samples in state["samples"].values()),
            "speeds": {interval: _summary(state["samples"][interval]) for interval in INTERVALS},
            "stalls": {"count": state["stall_count"], "last_at_ms": state["last_stall_at_ms"],
                       "slow_samples": state["slow_samples"]},
            "last_sample": state["last_sample"],
            "positions": {
                "checked": state["checked_count"],
                "median_abs_error": _median([abs(error) for error, _carried in errors]),
                "median_carried_ms": _median([carried for _error, carried in errors]),
            },
            "landmarks": [
                {"object_id": int(key), **item}
                for key, item in sorted(state["landmarks"].items(), key=lambda pair: pair[1]["bearing"])
            ],
        }

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        if shared_memory is not None:
            shared_memory.pop(STATE_KEY, None)
            for identifier in self.contract.belief_ids:
                shared_memory.pop(belief_key(identifier), None)

    def _seen(self, observation: Observation | None) -> dict[str, tuple[float, float]]:
        """Bearing and width of this frame's unclipped landmarks, by object id."""

        seen: dict[str, tuple[float, float]] = {}
        for thing in () if observation is None else observation.things:
            if not isinstance(thing, dict) or thing.get("source_plugin_id") != self.landmark_plugin_id:
                continue
            object_id = (thing.get("properties") or {}).get("object_id")
            bbox = (thing.get("location") or {}).get("bbox_xyxy_norm")
            if (
                not _is_int(object_id)
                or not isinstance(bbox, (list, tuple))
                or len(bbox) != 4
                or not all(_is_number(value) for value in bbox)
            ):
                continue
            left, _top, right, _bottom = bbox
            if left <= self.edge_margin or right >= 1 - self.edge_margin or right <= left:
                continue
            seen.setdefault(str(object_id), (round((left + right) / 2 - 0.5, 4), round(right - left, 4)))
        return seen

    def _same(self, item: dict[str, Any], width: float) -> bool:
        """Whether a box of ``width`` can be the remembered landmark ``item``."""

        return max(width, item["width"]) <= self.max_size_change * min(width, item["width"])

    def _measure(
        self, state: dict[str, Any], interval: str, seen: dict[str, tuple[float, float]], elapsed: int, now: int,
    ) -> float | None:
        """The view's speed over this interval, learned unless the turn was slow."""

        landmarks = state["landmarks"]
        speed = _median([
            (bearing - landmarks[key]["bearing"]) * 1000 / elapsed
            for key, (bearing, width) in seen.items()
            if key in landmarks and landmarks[key]["in_view"] and self._same(landmarks[key], width)
        ])
        if speed is None:
            return None
        learned = sorted(abs(value) for value in state["samples"][interval])
        slow_below = self.stall_fraction * _quantile(learned, 0.75) if interval.endswith(TURNING) and learned else None
        slow = slow_below is not None and abs(speed) < slow_below
        state["last_sample"] = {
            "interval": interval, "widths_per_s": round(speed, 4), "at_ms": now,
            "slow_below": None if slow_below is None else round(slow_below, 4), "learned": not slow,
        }
        if slow:
            state["slow_samples"] += 1
            state["slow_intervals"] += 1
            if state["slow_intervals"] == self.stall_intervals:
                state["stall_count"] += 1
                state["last_stall_at_ms"] = now
            return speed
        state["slow_intervals"] = 0
        samples = state["samples"][interval]
        samples.append(round(speed, 4))
        del samples[:-self.max_samples]
        return speed

    def _carry(
        self, state: dict[str, Any], interval: str, seen: dict[str, tuple[float, float]], elapsed: int,
        speed: float | None, memory: SharedMemory, now: int,
    ) -> None:
        """Move every landmark not seen at both ends by the view's speed over this interval."""

        if speed is None:
            speed = _median(state["samples"][interval])
            if speed is None and interval in _FALLBACK:
                speed = _median(state["samples"][_FALLBACK[interval]])
        straight = interval in (STRAIGHT, "reverse", "idle") or interval.endswith("." + TURN_END)
        kept: dict[str, dict[str, Any]] = {}
        for key, item in state["landmarks"].items():
            if not (item["in_view"] and key in seen and self._same(item, seen[key][1])):
                if speed is None:
                    _drop(state, item, "no_speed")
                    continue
                self._predict(state, memory, item, key, interval, elapsed, now)
                item["bearing"] = round(item["bearing"] + speed * elapsed / 1000, 4)
                item["in_view"] = False
                item["carried_ms"] += elapsed
                if straight:
                    item["straight_ms"] += elapsed
                if item["straight_ms"] > self.forget_straight_ms:
                    _drop(state, item, "straight_driving")
                    continue
            kept[key] = item
        state["landmarks"] = kept

    def _observe(self, state: dict[str, Any], seen: dict[str, tuple[float, float]], now: int, memory: SharedMemory) -> None:
        """Place each seen landmark, checking any carried bearing it had."""

        landmarks = state["landmarks"]
        for key, (bearing, width) in sorted(seen.items(), key=lambda pair: pair[1][0]):
            carried = landmarks.get(key)
            if carried is not None and not self._same(carried, width):
                _drop(state, carried, "size_changed")
                carried = None
            if carried is None:
                unseen = [
                    (abs(item["bearing"] - bearing), other) for other, item in landmarks.items()
                    if other not in seen and not item["in_view"] and self._same(item, width)
                    and abs(item["bearing"] - bearing) <= self.reacquire_within
                ]
                if unseen:
                    carried = landmarks.pop(min(unseen)[1])
            if carried is not None and not carried["in_view"]:
                prediction = carried.get("prediction")
                if prediction is not None:
                    belief = read_belief(memory, prediction["belief_id"])
                    trial = None if belief is None else next(
                        (trial for trial in belief.trials if trial.trial_id == prediction["trial_id"]), None)
                    if trial is not None:
                        low, high = trial.predicted
                        memory[belief_key(belief.belief_id)] = belief.resolved(trial.trial_id,
                            "support" if low <= bearing <= high else "contradict", bearing, now, self.max_samples)
                state["checked_count"] += 1
                errors = state["position_errors"]
                errors.append([round(bearing - carried["bearing"], 4), carried["carried_ms"]])
                del errors[:-self.max_samples]
            landmarks[key] = {
                "bearing": bearing, "width": width, "in_view": True, "seen_at_ms": now,
                "carried_ms": 0, "straight_ms": 0,
            }
        newest = sorted(landmarks.items(), key=lambda pair: pair[1]["seen_at_ms"], reverse=True)
        for _key, item in newest[self.max_landmarks:]:
            _drop(state, item, "displaced")
        state["landmarks"] = dict(newest[:self.max_landmarks])

    def _predict(self, state, memory, item, key, interval, elapsed, now):
        """Advance a prediction using only the belief available before this frame."""
        identifier = belief_id(interval)
        belief = read_belief(memory, identifier)
        previous = item.get("prediction")
        if previous is not None and previous["belief_id"] != identifier:
            _drop(state, item, "kind_changed")
            return
        if belief is None or not all(_is_number(belief.properties.get(name)) for name in ("p25", "p75")):
            _drop(state, item, "no_precision")
            return
        if not item["in_view"] and previous is None:
            return
        low, high = (belief.properties[name] * elapsed / 1000 for name in ("p25", "p75"))
        if previous is None:
            trial_id = f"landmark_{key}_{now}"
            trial = Trial(trial_id, "landmark_reappearance", now - elapsed,
                          [round(item["bearing"] + low, 4), round(item["bearing"] + high, 4)])
            belief = belief.opened(trial, self.max_landmarks)
            item["prediction"] = {"belief_id": identifier, "trial_id": trial_id}
        else:
            belief = replace(belief, trials=tuple(
                replace(trial, predicted=[round(trial.predicted[0] + low, 4), round(trial.predicted[1] + high, 4)])
                if trial.trial_id == previous["trial_id"] else trial for trial in belief.trials))
        memory[belief_key(identifier)] = belief

    def _publish(self, memory, state, now):
        dropped = state.pop("dropped")
        open_trials = {item["prediction"]["trial_id"] for item in state["landmarks"].values()
                       if "prediction" in item}
        for interval, samples in state["samples"].items():
            identifier = belief_id(interval)
            belief = read_belief(memory, identifier)
            if not samples:
                continue
            summary = _summary(samples)
            if belief is None:
                belief = Belief(identifier, summary["widths_per_s"], self.plugin_id, now)
            # A trial whose landmark was forgotten is withdrawn under the reason it was forgotten.
            closed: dict[str, list[str]] = {}
            for trial in belief.trials:
                if trial.trial_id not in open_trials:
                    closed.setdefault(dropped.get(trial.trial_id, "untracked"), []).append(trial.trial_id)
            for reason, trial_ids in closed.items():
                belief = belief.withdrawn(tuple(trial_ids), reason=reason)
            # Samples refine the estimate while an older, explicit prediction remains testable.
            memory[belief_key(identifier)] = replace(belief, value=summary["widths_per_s"],
                properties={key: summary[key] for key in ("p25", "p75", "samples")},
                updated_at_ms=max(now, belief.updated_at_ms))


def _drop(state: dict[str, Any], item: dict[str, Any], reason: str) -> None:
    """Forget a landmark's open prediction; publishing withdraws its trial under ``reason``."""

    prediction = item.pop("prediction", None)
    if prediction is not None:
        state["dropped"][prediction["trial_id"]] = reason


def _forget(state: dict[str, Any], reason: str) -> None:
    """Forget every landmark, so the next interval places each one afresh."""

    for item in state["landmarks"].values():
        _drop(state, item, reason)
    state["landmarks"] = {}


def _interval(motions: tuple[str, ...] | None) -> str | None:
    if not motions:
        return None
    kinds = set(motions)
    for motion in TURN_MOTIONS:
        straight = motion.split("_")[0]
        if kinds <= {motion, straight} and motion in kinds:
            phase = TURNING if kinds == {motion} else TURN_START if motions[-1] == motion else TURN_END
            return f"{motion}.{phase}"
    if len(kinds) == 1 and motions[0] in ("forward", "reverse", "idle"):
        return STRAIGHT if motions[0] == "forward" else motions[0]
    return None


def _captured_at(observation: Observation | None, sensor_id: str) -> int | None:
    """When the camera frame behind this observation was captured, if recorded."""

    if observation is None or not isinstance(observation.sensor_frame, dict):
        return None
    readings = observation.sensor_frame.get("readings")
    reading = readings.get(sensor_id) if isinstance(readings, dict) else None
    captured_at = reading.get("captured_at_ms") if isinstance(reading, dict) else None
    return captured_at if _is_int(captured_at) else None


def _restore(stored: Any) -> dict[str, Any]:
    """A clean copy of the stored model, or a fresh one that knows nothing yet."""

    stored = stored if isinstance(stored, dict) else {}
    samples = stored.get("samples") if isinstance(stored.get("samples"), dict) else {}
    landmarks: dict[str, dict[str, Any]] = {}
    recorded = stored.get("landmarks")
    for key, item in (recorded.items() if isinstance(recorded, dict) else ()):
        if (
            isinstance(key, str) and key.isdigit() and isinstance(item, dict)
            and _is_number(item.get("bearing")) and _is_number(item.get("width")) and item["width"] > 0
            and isinstance(item.get("in_view"), bool)
            and all(_is_int(item.get(name)) for name in ("seen_at_ms", "carried_ms", "straight_ms"))
        ):
            landmarks[key] = {name: item[name] for name in _LANDMARK_FIELDS}
            if isinstance(item.get("prediction"), dict):
                landmarks[key]["prediction"] = dict(item["prediction"])
    errors = stored.get("position_errors")
    return {
        "captured_at_ms": _int_or(stored.get("captured_at_ms"), None),
        "samples": {
            interval: [value for value in samples[interval] if _is_number(value)]
            if isinstance(samples.get(interval), (list, tuple)) else []
            for interval in INTERVALS
        },
        "last_interval": stored.get("last_interval"),
        "slow_intervals": _int_or(stored.get("slow_intervals"), 0),
        "slow_samples": _int_or(stored.get("slow_samples"), 0),
        "last_sample": stored.get("last_sample") if isinstance(stored.get("last_sample"), dict) else None,
        "stall_count": _int_or(stored.get("stall_count"), 0),
        "last_stall_at_ms": _int_or(stored.get("last_stall_at_ms"), None),
        "landmarks": landmarks,
        "checked_count": _int_or(stored.get("checked_count"), 0),
        "position_errors": [
            [error[0], error[1]] for error in (errors if isinstance(errors, (list, tuple)) else ())
            if isinstance(error, (list, tuple)) and len(error) == 2 and _is_number(error[0]) and _is_int(error[1])
        ],
        "updated_at_ms": _int_or(stored.get("updated_at_ms"), None),
        "dropped": {},
    }


def _summary(samples: list[float]) -> dict[str, Any]:
    """Median and quartiles of one kind of interval's speeds, in widths per second."""

    if not samples:
        return {"widths_per_s": None, "p25": None, "p75": None, "samples": 0}
    ordered = sorted(samples)
    return {
        "widths_per_s": _median(ordered),
        "p25": round(_quantile(ordered, 0.25), 4),
        "p75": round(_quantile(ordered, 0.75), 4),
        "samples": len(ordered),
    }


def _median(values: list[float]) -> float | None:
    return round(_quantile(sorted(values), 0.5), 4) if values else None


def _quantile(ordered: list[float], fraction: float) -> float:
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _int_or(value: Any, default: int | None) -> int | None:
    return value if _is_int(value) else default


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
