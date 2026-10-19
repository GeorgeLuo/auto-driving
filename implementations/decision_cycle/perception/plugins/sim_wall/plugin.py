"""Column segments of the Chase simulator's flat beige-gray walls.

Walls are told apart by saturation, (max - min) / max, which stays the same
when lighting brightens or darkens a wall face: the walls sit just below the
wood floor, well above the gray sky and well below the cardboard. The image is
split into vertical strips. In each strip, the longest run of mostly-wall rows
forms one box, so the box's lower edge is where the wall meets the floor: lower
in the image means nearer.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
    ViewLocation,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
)
from implementations.decision_cycle.perception.feeds.camera import (
    CameraFrame,
    FRONT_CAMERA_RGB_INPUT,
)
from implementations.decision_cycle.perception.shared.obstructions.boxes import zone


class SimWallPlugin:
    """Find simulator wall pixels by saturation and report one box per image strip."""

    plugin_id = "sim_wall"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        description="One image-space wall box per vertical strip that contains wall.",
        assumptions=(
            "walls are flat and less saturated than the wood floor",
            "the sky is near gray and the cardboard is strongly saturated",
            "the lower edge of a strip's wall run is where the wall meets the floor",
        ),
        emits=(
            "signal wall_in_sight",
            "one obstruction_evidence box per vertical strip containing wall",
        ),
        limitations=(
            "saturation thresholds are for this simulator's walls, not real scenes",
            "anything with wall-like saturation reads as wall",
            "no depth estimate and no identity across frames",
        ),
    )

    def __init__(
        self,
        *,
        min_saturation: float = 0.10,
        max_saturation: float = 0.173,
        strips: int = 16,
        row_fraction: float = 0.5,
        min_rows: int = 4,
        working_width: int = 160,
    ) -> None:
        for name, value in (
            ("min_saturation", min_saturation),
            ("max_saturation", max_saturation),
            ("row_fraction", row_fraction),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError(f"{name} must be a finite number in 0..1")
        if not min_saturation < max_saturation:
            raise ValueError("min_saturation must be below max_saturation")
        if row_fraction == 0:
            raise ValueError("row_fraction must be above 0")
        if not _is_int(strips) or not 1 <= strips <= 64:
            raise ValueError("strips must be an integer in 1..64")
        if not _is_int(min_rows) or min_rows < 1:
            raise ValueError("min_rows must be a positive integer")
        if not _is_int(working_width) or working_width < strips:
            raise ValueError("working_width must be an integer of at least strips")
        self.min_saturation = float(min_saturation)
        self.max_saturation = float(max_saturation)
        self.strips = int(strips)
        self.row_fraction = float(row_fraction)
        self.min_rows = int(min_rows)
        self.working_width = int(working_width)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        working = cv2.blur(_resize_rgb(frame.rgb, self.working_width).astype(np.float32), (3, 3))
        brightest = working.max(axis=2)
        saturation = (brightest - working.min(axis=2)) / np.maximum(brightest, 1.0)
        mask = (saturation >= self.min_saturation) & (saturation <= self.max_saturation)
        boxes = _strip_boxes(mask, self.strips, self.row_fraction, self.min_rows)
        return PerceptionEvidenceBatch(
            signals=(PerceptionSignal(
                signal_id="wall_in_sight",
                value=bool(boxes),
                properties={
                    "segment_count": len(boxes),
                    "lowest_bottom_y": max((box[3] for box in boxes), default=None),
                },
            ),),
            things=tuple(_wall_thing(box, index) for index, box in enumerate(boxes)),
            measurements={
                "working_width": int(working.shape[1]),
                "working_height": int(working.shape[0]),
                "wall_fraction": float(mask.mean()),
            },
        )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _resize_rgb(rgb: np.ndarray, working_width: int) -> np.ndarray:
    height, width = rgb.shape[:2]
    if width <= working_width:
        return rgb
    resized = cv2.resize(
        rgb,
        (working_width, max(1, int(round(height * working_width / width)))),
        interpolation=cv2.INTER_AREA,
    )
    return np.ascontiguousarray(resized)


def _strip_boxes(
    mask: np.ndarray, strips: int, row_fraction: float, min_rows: int,
) -> tuple[tuple[float, float, float, float], ...]:
    """Per strip, the longest run of rows that are at least ``row_fraction`` wall."""

    height, width = mask.shape
    edges = np.linspace(0, width, strips + 1).round().astype(int)
    boxes = []
    for left, right in zip(edges, edges[1:]):
        rows = mask[:, left:right].mean(axis=1) >= row_fraction
        run = _longest_run(rows)
        if run is not None and run[1] - run[0] >= min_rows:
            boxes.append((left / width, run[0] / height, right / width, run[1] / height))
    return tuple(boxes)


def _longest_run(rows: np.ndarray) -> tuple[int, int] | None:
    """The first ``[start, stop)`` among the longest runs of True, or None."""

    padded = np.concatenate(([False], rows, [False])).astype(np.int8)
    changes = np.flatnonzero(np.diff(padded))
    if not changes.size:
        return None
    starts, stops = changes[::2], changes[1::2]
    longest = int(np.argmax(stops - starts))
    return int(starts[longest]), int(stops[longest])


def _wall_thing(bbox: tuple[float, float, float, float], index: int) -> PerceivedThing:
    return PerceivedThing(
        thing_id=f"wall_segment_{index:03d}",
        kind="obstruction_evidence",
        label="wall",
        location=ViewLocation(frame="image", zone=zone(bbox), bbox_xyxy_norm=bbox),
        properties={"evidence": "wall_saturation"},
    )
