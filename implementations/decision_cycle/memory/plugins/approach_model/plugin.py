"""Learn how fast things ahead approach while the car drives straight, and how long it drives before it must stop.

Everything is in the car's own units, image rows and milliseconds on the
camera's capture clock: no camera model, no distance and no map. A thing
standing on the floor meets it at its box's lower edge, and while the car
drives straight that edge steps down the image one row at a time, slowly near
the horizon and quickly near the car. Rows are ``row_bins`` equal parts of the
image height, which should be no finer than the detectors' own rows; row
``k`` holds lower edges from ``k / row_bins`` up to the next row.

Each new camera frame (``sensor_id``'s capture time) closes one interval. It
is straight driving only when applied controls cover the whole interval and
all drive straight forward, with no static view. An edge is a box from one of
``edge_plugin_ids`` that overlaps the band ``min_x``..``max_x`` ahead, kept by
its ``object_id`` property or else by its left edge. A lower edge within
``edge_margin`` of the image bottom is clipped and is not an edge. When an
edge steps down exactly one row during straight driving, the time it spent on
its previous row is one sample of that row's milliseconds. The clock starts at
an edge's first step, so an edge first seen part way through a row, or one
that never moves (a wall alongside the car), teaches nothing; any other move is
a different thing behind the same box and restarts it, and any other interval
stops every clock. Each row keeps its last ``max_samples`` samples and its
learned time is their median.

A run is straight driving from its first interval until the command changes. A
run that ends with applied straight reverse for one of ``stop_reasons`` is how long the car drove
before it had to stop; when the view was already static, the static time is
taken off. The last ``max_samples`` runs are kept and ``run_ms`` is their
median. Runs ended any other way are not counted, so a later proposal that
turns away earlier does not shorten what the car learned it can drive.
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
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID
from implementations.decision_cycle.memory.shared.motion.control_applications import application_at, motion_at, motions_over

ROWS_BELIEF_ID = "approach_model.ms_per_row"
RUN_BELIEF_ID = "approach_model.run_ms"
STATE_KEY = "memory.approach_model.state"
_EPS = 1e-9


class ApproachModel:
    plugin_id = "approach_model"
    contract = MemoryPluginContract(
        description="Time forward approaches and check predicted arrival and run duration.",
        assumptions=("a box's lower edge meets the floor", "positive throttle drives forward",
                     "stop_reasons identify necessary stops", "forward throttle magnitudes are comparable"),
        belief_ids=(ROWS_BELIEF_ID, RUN_BELIEF_ID),
        limitations=("requires a covered forward-control interval", "arrival checks require every intervening row"),
    )

    def __init__(
        self,
        *,
        edge_plugin_ids: tuple[str, ...] | list[str] = ("sim_wall", "sim_cardboard_permanence"),
        sensor_id: str = FRONT_CAMERA_SENSOR_ID,
        stop_reasons: tuple[str, ...] | list[str] = ("avoid_back_up_until_right_clear",),
        min_x: float = 0.3,
        max_x: float = 0.7,
        row_bins: int = 120,
        edge_margin: float = 0.01,
        max_samples: int = 64,
        max_interval_ms: int = 250,
    ) -> None:
        if (
            not isinstance(edge_plugin_ids, (tuple, list))
            or not edge_plugin_ids
            or not all(isinstance(plugin_id, str) and plugin_id for plugin_id in edge_plugin_ids)
        ):
            raise ValueError("edge_plugin_ids must be a non-empty list of plugin ids")
        if (not isinstance(stop_reasons, (tuple, list)) or not stop_reasons
                or not all(isinstance(reason, str) and reason for reason in stop_reasons)):
            raise ValueError("stop_reasons must be a non-empty list of applied control reasons")
        for name, value in (("sensor_id", sensor_id),):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        for name, value in (
            ("row_bins", row_bins), ("max_samples", max_samples), ("max_interval_ms", max_interval_ms),
        ):
            if not _is_int(value) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name, value, low, high in (
            ("min_x", min_x, 0.0, 1.0), ("max_x", max_x, 0.0, 1.0), ("edge_margin", edge_margin, 0.0, 0.5),
        ):
            if not _is_number(value) or not low <= value <= high:
                raise ValueError(f"{name} must be a finite number in [{low:g}, {high:g}]")
        if not min_x < max_x:
            raise ValueError("min_x must be below max_x")
        self.edge_plugin_ids = tuple(edge_plugin_ids)
        self.sensor_id = sensor_id
        self.stop_reasons = tuple(stop_reasons)
        self.min_x = float(min_x)
        self.max_x = float(max_x)
        self.row_bins = row_bins
        self.edge_margin = float(edge_margin)
        self.max_samples = max_samples
        self.max_interval_ms = max_interval_ms

    def update(self, context: DecisionFrameContext, observation: Observation | None) -> None:
        shared_memory = context.shared_memory
        if shared_memory is None:
            raise ValueError("approach_model requires a shared-memory map")
        state = _restore(shared_memory.get(STATE_KEY), self.row_bins)
        motion = motion_at(shared_memory, context.timestamp_ms)
        previous_update = state["updated_at_ms"]
        if previous_update is not None and motions_over(shared_memory, previous_update, context.timestamp_ms) is None:
            motion = None
        straight = motion == "forward" and not state["static"]
        application = application_at(shared_memory, context.timestamp_ms)
        stopped = (motion == "reverse" and application is not None
                   and application.control.reason in self.stop_reasons)
        ended_run = False
        if not straight:
            if stopped and state["straight"] and state["run_started_at_ms"] is not None:
                ended_run = self._end_run(state) is not None
            state["run_started_at_ms"] = None
        state["bent"] = state["bent"] or not straight
        captured_at = _captured_at(observation, self.sensor_id)
        previous_at = state["captured_at_ms"]
        covered = straight
        if captured_at is not None and previous_at is not None and captured_at < previous_at:
            state["edges"] = {}
            state["run_started_at_ms"] = None
            state["captured_at_ms"] = None
            previous_at = None
            state["bent"] = True
        if captured_at is not None and (previous_at is None or captured_at > previous_at):
            elapsed = None if previous_at is None else captured_at - previous_at
            motions = None if previous_at is None else motions_over(shared_memory, previous_at, captured_at)
            covered = elapsed is not None and elapsed <= self.max_interval_ms and not state["bent"] and motions == ("forward",)
            if covered:
                if state["run_started_at_ms"] is None:
                    state["run_started_at_ms"] = previous_at
                self._step(state, self._edges(observation), captured_at)
            else:
                state["run_started_at_ms"] = None
                state["edges"] = {key: {"row": row, "since_ms": None} for key, row in self._edges(observation).items()}
            state["captured_at_ms"] = captured_at
            state["bent"] = not straight
        static, unchanged_ms = _view(observation)
        state["straight"] = straight
        state["static"] = static
        state["unchanged_ms"] = unchanged_ms
        if captured_at is not None:
            state["frame_at_ms"] = captured_at
        self._beliefs(shared_memory, state, captured_at, covered, ended_run,
                      context.timestamp_ms)
        state["updated_at_ms"] = context.timestamp_ms
        state.pop("ms_per_row")
        state.pop("run_ms")
        shared_memory[STATE_KEY] = state

    def status(self, shared_memory: SharedMemory | None) -> dict[str, Any]:
        stored = None if shared_memory is None else shared_memory.get(STATE_KEY)
        state = _restore(stored, self.row_bins)
        return {
            "health": LEDGER_HEALTH_EMPTY if stored is None else LEDGER_HEALTH_HEALTHY,
            "record_count": sum(len(samples) for samples in state["samples"].values()) + len(state["runs"]),
            "row_bins": state["row_bins"],
            "rows": [
                {"row": round(int(key) / state["row_bins"], 4), "ms_per_row": state["ms_per_row"][key],
                 "samples": len(samples)}
                for key, samples in sorted(state["samples"].items(), key=lambda pair: int(pair[0]))
            ],
            "run_ms": state["run_ms"],
            "runs": len(state["runs"]),
            "edges": len(state["edges"]),
        }

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        if shared_memory is not None:
            shared_memory.pop(STATE_KEY, None)
            for belief_id in self.contract.belief_ids:
                shared_memory.pop(belief_key(belief_id), None)

    def _beliefs(self, memory, state, captured_at, straight, ended_run, now):
        """Keep precision separate from checks of predictions made before this frame."""
        row_belief = read_belief(memory, ROWS_BELIEF_ID)
        run_belief = read_belief(memory, RUN_BELIEF_ID)
        edges = state["edges"]
        if row_belief is not None:
            same_rows = row_belief.properties.get("row_bins") == self.row_bins
            if not same_rows and not state["samples"]:
                memory.pop(belief_key(ROWS_BELIEF_ID), None)
            if not same_rows or not straight:
                row_belief = replace(row_belief, trials=(), checks=row_belief.checks if same_rows else ())
            elif captured_at is not None and row_belief.trials:
                trial = row_belief.trials[0]
                edge = edges.get(trial.properties["edge"])
                if edge is None:
                    row_belief = replace(row_belief, trials=())
                elif edge["row"] >= trial.properties["target"]:
                    elapsed = captured_at - trial.started_at_ms
                    low, high = trial.predicted
                    row_belief = row_belief.resolved(trial.trial_id,
                        "support" if low <= elapsed <= high else "contradict", elapsed, captured_at, self.max_samples)
        if state["samples"]:
            precision = {key: _precision(values) for key, values in state["samples"].items()}
            properties = {"row_bins": self.row_bins, "rows": precision}
            if row_belief is None:
                row_belief = Belief(ROWS_BELIEF_ID, state["ms_per_row"], self.plugin_id, now)
            row_belief = replace(row_belief, value=state["ms_per_row"], properties=properties,
                                 updated_at_ms=max(now, row_belief.updated_at_ms))
            stop = read_belief(memory, "stop_row.row")
            if straight and captured_at is not None and not row_belief.trials and stop is not None and edges:
                edge_id, edge = max(edges.items(), key=lambda pair: pair[1]["row"])
                target = math.ceil(stop.value * self.row_bins - _EPS)
                rows = [precision.get(str(index)) for index in range(edge["row"], target)]
                if rows and all(row is not None for row in rows):
                    prediction = [sum(row[name] for row in rows) for name in ("p25", "p75")]
                    row_belief = row_belief.opened(Trial(f"arrival_{captured_at}", "arrival", captured_at,
                        prediction, {"edge": edge_id, "target": target}), 1)
            memory[belief_key(ROWS_BELIEF_ID)] = row_belief
        if run_belief is not None:
            if ended_run and run_belief.trials:
                trial = run_belief.trials[0]
                duration = state["runs"][-1]
                low, high = trial.predicted
                run_belief = run_belief.resolved(trial.trial_id,
                    "support" if low <= duration <= high else "contradict", duration, now, self.max_samples)
            elif not straight:
                run_belief = replace(run_belief, trials=())
        if state["runs"]:
            if run_belief is None:
                run_belief = Belief(RUN_BELIEF_ID, state["run_ms"], self.plugin_id, now)
            run_belief = replace(run_belief, value=state["run_ms"], properties=_precision(state["runs"]),
                                 updated_at_ms=max(now, run_belief.updated_at_ms))
            start = state["run_started_at_ms"]
            if straight and start is not None and not run_belief.trials:
                run_belief = run_belief.opened(Trial(f"run_{start}", "run_duration", start,
                    [run_belief.properties["p25"], run_belief.properties["p75"]]), 1)
            memory[belief_key(RUN_BELIEF_ID)] = run_belief

    def _edges(self, observation: Observation | None) -> dict[str, int]:
        """Rows of this frame's unclipped lower edges ahead, by identity."""

        edges: dict[str, int] = {}
        for thing in () if observation is None else observation.things:
            if not isinstance(thing, dict) or thing.get("source_plugin_id") not in self.edge_plugin_ids:
                continue
            bbox = (thing.get("location") or {}).get("bbox_xyxy_norm")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or not all(_is_number(value) for value in bbox):
                continue
            left, _top, right, bottom = bbox
            if right <= self.min_x or left >= self.max_x or bottom >= 1 - self.edge_margin:
                continue
            object_id = (thing.get("properties") or {}).get("object_id")
            name = f"#{object_id}" if _is_int(object_id) else f"@{round(left, 4)}"
            edges.setdefault(f"{thing['source_plugin_id']}{name}", math.floor(bottom * self.row_bins + _EPS))
        return edges

    def _step(self, state: dict[str, Any], seen: dict[str, int], now: int) -> None:
        """Time each edge that stepped down one row since the previous frame; restart the rest."""

        edges: dict[str, dict[str, Any]] = {}
        for key, row in seen.items():
            last = state["edges"].get(key)
            if last is not None and row == last["row"]:
                edges[key] = last
            elif last is not None and row == last["row"] + 1:
                if last["since_ms"] is not None:
                    samples = state["samples"].setdefault(str(last["row"]), [])
                    samples.append(now - last["since_ms"])
                    del samples[:-self.max_samples]
                    state["ms_per_row"][str(last["row"])] = _median(samples)
                edges[key] = {"row": row, "since_ms": now}
            else:
                edges[key] = {"row": row, "since_ms": None}
        state["edges"] = edges

    def _end_run(self, state: dict[str, Any]) -> int | None:
        end = state["frame_at_ms"]
        if end is None:
            return
        if state["static"]:
            end -= state["unchanged_ms"]
        run = end - state["run_started_at_ms"]
        if run > 0:
            state["runs"].append(run)
            del state["runs"][:-self.max_samples]
            state["run_ms"] = _median(state["runs"])
            return run


def _view(observation: Observation | None) -> tuple[bool, int]:
    """Whether this observation's view is static, and for how long it has not changed."""

    for signal in () if observation is None else observation.signals:
        if isinstance(signal, dict) and signal.get("signal_id") == "view_static":
            unchanged = (signal.get("properties") or {}).get("unchanged_for_ms")
            return signal.get("value") is True, unchanged if _is_int(unchanged) and unchanged >= 0 else 0
    return False, 0


def _captured_at(observation: Observation | None, sensor_id: str) -> int | None:
    """When the camera frame behind this observation was captured, if recorded."""

    if observation is None or not isinstance(observation.sensor_frame, dict):
        return None
    readings = observation.sensor_frame.get("readings")
    reading = readings.get(sensor_id) if isinstance(readings, dict) else None
    captured_at = reading.get("captured_at_ms") if isinstance(reading, dict) else None
    return captured_at if _is_int(captured_at) else None


def _restore(stored: Any, row_bins: int) -> dict[str, Any]:
    """A clean copy of the stored model, or a fresh one that knows nothing yet.

    Rows learned at another ``row_bins`` are not comparable, so they are dropped.
    """

    stored = stored if isinstance(stored, dict) else {}
    same_rows = stored.get("row_bins") == row_bins
    recorded = stored.get("samples") if same_rows and isinstance(stored.get("samples"), dict) else {}
    samples = {
        key: [value for value in values if _is_int(value) and value > 0]
        for key, values in recorded.items()
        if isinstance(key, str) and key.isdigit() and isinstance(values, (list, tuple))
    }
    samples = {key: values for key, values in samples.items() if values}
    edges = stored.get("edges") if same_rows and isinstance(stored.get("edges"), dict) else {}
    runs = stored.get("runs")
    runs = [run for run in (runs if isinstance(runs, (list, tuple)) else ()) if _is_int(run) and run > 0]
    return {
        "row_bins": row_bins,
        "captured_at_ms": _int_or(stored.get("captured_at_ms"), None),
        "frame_at_ms": _int_or(stored.get("frame_at_ms"), None),
        "bent": stored.get("bent") is True,
        "straight": stored.get("straight") is True,
        "static": stored.get("static") is True,
        "unchanged_ms": _int_or(stored.get("unchanged_ms"), 0),
        "edges": {
            key: {"row": item["row"], "since_ms": _int_or(item.get("since_ms"), None)}
            for key, item in edges.items()
            if isinstance(key, str) and isinstance(item, dict) and _is_int(item.get("row"))
        },
        "samples": samples,
        "ms_per_row": {key: _median(values) for key, values in samples.items()},
        "run_started_at_ms": _int_or(stored.get("run_started_at_ms"), None),
        "runs": runs,
        "run_ms": _median(runs),
        "updated_at_ms": _int_or(stored.get("updated_at_ms"), None),
    }


def _precision(values):
    ordered = sorted(values)
    def quantile(fraction):
        position = fraction * (len(ordered) - 1)
        low = math.floor(position)
        high = min(low + 1, len(ordered) - 1)
        return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 2)
    return {"p25": quantile(.25), "p75": quantile(.75), "samples": len(values)}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    value = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return round(value, 2)


def _int_or(value: Any, default: int | None) -> int | None:
    return value if _is_int(value) else default


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
