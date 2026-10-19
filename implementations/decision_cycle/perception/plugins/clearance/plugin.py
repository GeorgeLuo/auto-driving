"""Report whether a near obstacle box blocks the view ahead or on the right.

A box blocks a band when its x-range overlaps the band and its lower edge is at
or below that band's ``*_bottom_y``: low in the image means near the car. The
ahead band counts farther boxes than the right band, so after backing away from
an obstacle the right can clear while the obstacle still blocks the way ahead.
"""

from __future__ import annotations

import math

from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
)


class ClearancePlugin:
    """Check two image bands against this frame's near detection boxes."""

    plugin_id = "clearance"
    contract = PerceptionPluginContract(
        description="Report whether a near obstacle box blocks the view ahead or on the right.",
        assumptions=(
            "the detection plugins run earlier in the same selection",
            "a box whose lower edge is low in the image is near the car",
            "positive image x is the car's right",
        ),
        emits=("signal clearance_ahead", "signal clearance_right"),
        limitations=(
            "image-space bands only; no depth or vehicle width",
            "only the detection plugins' boxes can block a band, so a missed obstacle reads as clear",
        ),
    )

    def __init__(
        self,
        *,
        detection_plugin_ids: tuple[str, ...] | list[str] = ("sim_cardboard_obstacle", "sim_wall"),
        ahead_min_x: float = 0.3,
        ahead_max_x: float = 0.7,
        ahead_bottom_y: float = 0.55,
        right_min_x: float = 0.65,
        right_bottom_y: float = 0.6,
    ) -> None:
        if (
            not isinstance(detection_plugin_ids, (tuple, list))
            or not detection_plugin_ids
            or not all(isinstance(plugin_id, str) and plugin_id for plugin_id in detection_plugin_ids)
        ):
            raise ValueError("detection_plugin_ids must be a non-empty list of plugin ids")
        for name, value in (
            ("ahead_min_x", ahead_min_x),
            ("ahead_max_x", ahead_max_x),
            ("ahead_bottom_y", ahead_bottom_y),
            ("right_min_x", right_min_x),
            ("right_bottom_y", right_bottom_y),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError(f"{name} must be a finite number in 0..1")
        if not ahead_min_x < ahead_max_x:
            raise ValueError("ahead_min_x must be below ahead_max_x")
        if not right_min_x < 1:
            raise ValueError("right_min_x must be below 1")
        self.detection_plugin_ids = tuple(detection_plugin_ids)
        self.ahead_min_x = float(ahead_min_x)
        self.ahead_max_x = float(ahead_max_x)
        self.ahead_bottom_y = float(ahead_bottom_y)
        self.right_min_x = float(right_min_x)
        self.right_bottom_y = float(right_bottom_y)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        boxes = _detection_boxes(inputs.prior_things, self.detection_plugin_ids)
        return PerceptionEvidenceBatch(signals=(
            _band_signal("clearance_ahead", boxes, self.ahead_min_x, self.ahead_max_x, self.ahead_bottom_y),
            _band_signal("clearance_right", boxes, self.right_min_x, 1.0, self.right_bottom_y),
        ))


def _band_signal(
    signal_id: str,
    boxes: tuple[tuple[float, float, float, float], ...],
    min_x: float,
    max_x: float,
    bottom_y: float,
) -> PerceptionSignal:
    blocking = sum(1 for box in boxes if box[0] < max_x and box[2] > min_x and box[3] >= bottom_y)
    return PerceptionSignal(
        signal_id=signal_id,
        value=blocking == 0,
        properties={"min_x": min_x, "max_x": max_x, "bottom_y": bottom_y, "blocking_count": blocking},
    )


def _detection_boxes(
    things: tuple[PerceivedThing, ...], plugin_ids: tuple[str, ...],
) -> tuple[tuple[float, float, float, float], ...]:
    """This frame's boxes from the detection plugins."""

    return tuple(
        tuple(float(value) for value in thing.location.bbox_xyxy_norm)
        for thing in things
        if thing.source_plugin_id in plugin_ids
        and thing.kind == "obstruction_evidence"
        and thing.location.bbox_xyxy_norm is not None
    )
