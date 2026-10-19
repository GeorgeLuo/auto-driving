"""Report whether the nearest box ahead and on the right is near or far, by how long the car takes to reach it.

Memory's ``approach_model`` learns how many milliseconds a lower edge spends on
each image row while the car drives straight, and ``run_ms``, how long it
usually drives straight before it has to stop. Memory's ``stop_row`` learns
the row where it stops. A box's reach is the time its lower edge would take to
descend from its row to the stop row: the sum of the learned times of the rows
between. The box is near when it would be reached within one usual run, and
far otherwise, so nothing here is tuned: both numbers are the car's own.

Each band reports its lowest lower edge, the nearest box, as clearance does.
A row with no learned time borrows the mean of the learned rows on either side
of it; the row holding the stop row is seldom timed, since the car stops
there. When rows are still unknown and the known ones already exceed one run
the box is far; otherwise it is unknown. The signal value is ``"near"``, ``"far"`` or None
(nothing in the band, or not yet learned), and ``reason`` says which.
"""

from __future__ import annotations

import math
from typing import Any

from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
)

NEAR = "near"
FAR = "far"
_EPS = 1e-9


class ClosenessPlugin:
    """Classify the nearest box in two image bands as near or far."""

    plugin_id = "closeness"
    contract = PerceptionPluginContract(
        memory_required=True,
        description="Report whether the nearest box ahead and on the right is near or far, from learned approach times.",
        assumptions=(
            "the detection plugins run earlier in the same selection",
            "memory's approach_model and stop_row publish to shared memory",
            "a box's lower edge is where it meets the floor",
        ),
        emits=("signal closeness_ahead", "signal closeness_right"),
        limitations=(
            "reads the memory published by the previous cycle",
            "times are learned while driving straight, so they say nothing about turning toward a box",
            "unknown until the car has stopped at least once and timed the rows between",
        ),
    )

    def __init__(
        self,
        *,
        detection_plugin_ids: tuple[str, ...] | list[str] = ("sim_cardboard_obstacle", "sim_wall"),
        model_key: str = "memory.approach_model.state",
        stop_row_key: str = "memory.stop_row.hypothesis",
        ahead_min_x: float = 0.3,
        ahead_max_x: float = 0.7,
        right_min_x: float = 0.65,
    ) -> None:
        if (
            not isinstance(detection_plugin_ids, (tuple, list))
            or not detection_plugin_ids
            or not all(isinstance(plugin_id, str) and plugin_id for plugin_id in detection_plugin_ids)
        ):
            raise ValueError("detection_plugin_ids must be a non-empty list of plugin ids")
        for name, value in (("model_key", model_key), ("stop_row_key", stop_row_key)):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        for name, value in (("ahead_min_x", ahead_min_x), ("ahead_max_x", ahead_max_x), ("right_min_x", right_min_x)):
            if not _is_number(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite number in 0..1")
        if not ahead_min_x < ahead_max_x:
            raise ValueError("ahead_min_x must be below ahead_max_x")
        if not right_min_x < 1:
            raise ValueError("right_min_x must be below 1")
        self.detection_plugin_ids = tuple(detection_plugin_ids)
        self.model_key = model_key
        self.stop_row_key = stop_row_key
        self.ahead_min_x = float(ahead_min_x)
        self.ahead_max_x = float(ahead_max_x)
        self.right_min_x = float(right_min_x)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        memory = inputs.shared_memory
        if memory is None:
            raise ValueError(f"{self.plugin_id} requires host shared memory")
        model = memory.get(self.model_key)
        model = model if isinstance(model, dict) else {}
        hypothesis = memory.get(self.stop_row_key)
        stop_row = hypothesis.get("stop_row") if isinstance(hypothesis, dict) else None
        learned = _Learned(
            model.get("ms_per_row"), model.get("row_bins"), model.get("run_ms"),
            stop_row if _is_number(stop_row) else None,
        )
        boxes = tuple(
            tuple(float(value) for value in thing.location.bbox_xyxy_norm)
            for thing in inputs.prior_things
            if _is_box(thing, self.detection_plugin_ids)
        )
        return PerceptionEvidenceBatch(signals=(
            learned.signal("closeness_ahead", boxes, self.ahead_min_x, self.ahead_max_x),
            learned.signal("closeness_right", boxes, self.right_min_x, 1.0),
        ))


class _Learned:
    """What memory has learned, read once per frame."""

    def __init__(self, ms_per_row: Any, row_bins: Any, run_ms: Any, stop_row: float | None) -> None:
        self.rates = {
            int(key): float(value) for key, value in (ms_per_row.items() if isinstance(ms_per_row, dict) else ())
            if isinstance(key, str) and key.isdigit() and _is_number(value) and value > 0
        }
        self.row_bins = row_bins if isinstance(row_bins, int) and not isinstance(row_bins, bool) and row_bins > 0 else None
        self.run_ms = float(run_ms) if _is_number(run_ms) and run_ms > 0 else None
        self.stop_row = stop_row

    def signal(self, signal_id: str, boxes: tuple[tuple[float, ...], ...], min_x: float, max_x: float) -> PerceptionSignal:
        row = max((box[3] for box in boxes if box[0] < max_x and box[2] > min_x), default=None)
        properties: dict[str, Any] = {
            "min_x": min_x, "max_x": max_x, "row": row, "stop_row": self.stop_row,
            "run_ms": self.run_ms, "reach_ms": None,
        }
        value = None
        if row is None:
            reason = "nothing_in_band"
        elif self.stop_row is None or self.run_ms is None or self.row_bins is None:
            reason = "not_learned"
        else:
            reach, complete = self._reach(row)
            properties["reach_ms"] = round(reach)
            if not complete and reach <= self.run_ms:
                reason = "not_learned"
            else:
                value = NEAR if reach <= self.run_ms else FAR
                reason = "within_one_run" if value == NEAR else "beyond_one_run"
        properties["reason"] = reason
        return PerceptionSignal(signal_id=signal_id, value=value, properties=properties)

    def _reach(self, row: float) -> tuple[float, bool]:
        """Milliseconds from ``row`` down to the stop row, and whether every row between was known."""

        total, complete = 0.0, True
        index = math.floor(row * self.row_bins + _EPS)
        while index / self.row_bins < self.stop_row - _EPS:
            top = max(row, index / self.row_bins)
            bottom = min(self.stop_row, (index + 1) / self.row_bins)
            rate = self._rate(index)
            if rate is None:
                complete = False
            else:
                total += (bottom - top) * self.row_bins * rate
            index += 1
        return total, complete

    def _rate(self, index: int) -> float | None:
        if index in self.rates:
            return self.rates[index]
        neighbours = [self.rates[other] for other in (index - 1, index + 1) if other in self.rates]
        return sum(neighbours) / len(neighbours) if neighbours else None


def _is_box(thing: PerceivedThing, plugin_ids: tuple[str, ...]) -> bool:
    return (
        thing.source_plugin_id in plugin_ids
        and thing.kind == "obstruction_evidence"
        and thing.location.bbox_xyxy_norm is not None
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
