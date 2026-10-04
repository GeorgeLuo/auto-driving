"""Scripted replay of the perception ``multi_obstruction_tracks`` plugin.

The plugin runs over fixed frames with one run-owned map. ``tracks_replay``
returns the tracked things and track events after every frame; they are pinned
in ``baselines/`` so a change that should not alter the tracks shows as an
unchanged replay. Floats are rounded to four decimals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autonomy.decision_cycle.perception.diagnostics.sink import PerceptionDiagnosticSink
from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch
from autonomy.decision_cycle.perception.plugin import PerceptionPluginInputs
from implementations.decision_cycle.perception.feeds.camera import CameraFrame
from implementations.decision_cycle.perception.plugins.multi_obstruction_tracks.plugin import (
    MultiObstructionTracksPlugin,
)

BASELINES = Path(__file__).with_name("baselines")
IMAGE_WIDTH, IMAGE_HEIGHT = 320, 240
# Left edge of the obstruction per frame; None is a frame with nothing in view.
OBSTRUCTION_LEFT_EDGES = (0.20, 0.23, 0.26, 0.29, 0.32, None, None, 0.40, 0.43)


class QuietDiagnostics(PerceptionDiagnosticSink):
    def __init__(self) -> None:
        super().__init__(output_dir=None, plugin_id="replay", allowed_artifacts=())


def obstruction_image(left_edge: float | None) -> np.ndarray:
    """RGB frame: a bright textured-edge box on a dark background, or nothing."""

    rgb = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 60, dtype=np.uint8)
    if left_edge is not None:
        top_left = (int(left_edge * IMAGE_WIDTH), int(0.30 * IMAGE_HEIGHT))
        bottom_right = (int((left_edge + 0.22) * IMAGE_WIDTH), int(0.62 * IMAGE_HEIGHT))
        cv2.rectangle(rgb, top_left, bottom_right, (200, 200, 200), -1)
        cv2.rectangle(rgb, top_left, bottom_right, (255, 255, 255), 2)
    return rgb


def perceive_frame(
    plugin: MultiObstructionTracksPlugin,
    index: int,
    left_edge: float | None,
    shared_memory: dict[str, Any] | None,
) -> PerceptionEvidenceBatch:
    """One frame of the obstruction image through ``plugin`` with the given map."""

    frame_id = f"frame_{index}"
    timestamp_ms = 100 + index * 100
    camera_frame = CameraFrame(
        "front_camera",
        timestamp_ms,
        np.ascontiguousarray(obstruction_image(left_edge)),
        None,
        {"source_id": frame_id},
    )
    return plugin.perceive(
        PerceptionPluginInputs(
            frame_id,
            timestamp_ms,
            {"frame": camera_frame},
            QuietDiagnostics(),
            {"sequence_index": index},
            shared_memory=shared_memory,
        )
    )


def tracked_frame(batch: PerceptionEvidenceBatch) -> dict[str, Any]:
    """The tracked things and events of one frame, in the pinned shape."""

    return canonical(
        {
            "things": [
                {
                    "thing_id": thing.thing_id,
                    "bbox_xyxy_norm": thing.location.bbox_xyxy_norm,
                    "confidence": thing.confidence,
                }
                for thing in batch.things
            ],
            "track_events": {
                str(track_id): event for track_id, event in batch.measurements["track_events"].items()
            },
        }
    )


def tracks_replay() -> list[dict[str, Any]]:
    """An obstruction appears, moves, leaves view for two frames, and returns."""

    plugin = MultiObstructionTracksPlugin()
    shared_memory: dict[str, Any] = {}
    return [
        {
            "frame_id": f"frame_{index}",
            "tracked_observation": tracked_frame(
                perceive_frame(plugin, index, left_edge, shared_memory)
            ),
        }
        for index, left_edge in enumerate(OBSTRUCTION_LEFT_EDGES)
    ]


def canonical(value: Any) -> Any:
    """JSON-comparable form of a replay: tuples as lists, floats rounded."""

    def rounded(item: Any) -> Any:
        if isinstance(item, float):
            return round(item, 4)
        if isinstance(item, dict):
            return {key: rounded(inner) for key, inner in item.items()}
        if isinstance(item, (list, tuple)):
            return [rounded(inner) for inner in item]
        return item

    return rounded(json.loads(json.dumps(value, sort_keys=True)))


def load_baseline(name: str) -> Any:
    return json.loads((BASELINES / f"{name}.json").read_text(encoding="utf-8"))
