"""Scripted replays of the packaged memory plugins, and their pinned reports.

Each replay drives a ``MemoryRunner`` over fixed inputs and returns the
``memory_report_v0`` after every frame. The reports are pinned in ``baselines/``
so a change that should not alter what memory publishes (a move, a rename)
shows as an unchanged report; a change that does alter it edits the baseline in
the same commit. Floats are rounded to four decimals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.diagnostics.sink import PerceptionDiagnosticSink
from autonomy.decision_cycle.perception.plugin import PerceptionPluginInputs
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import packaged_activation
from implementations.decision_cycle.perception.feeds.camera import CameraFrame
from implementations.decision_cycle.perception.plugins.multi_obstruction_tracks.plugin import (
    MultiObstructionTracksPlugin,
)


BASELINES = Path(__file__).with_name("baselines")
IMAGE_WIDTH, IMAGE_HEIGHT = 320, 240
# Left edge of the obstruction per frame; None is a frame with nothing in view.
OBSTRUCTION_LEFT_EDGES = (0.20, 0.23, 0.26, 0.29, 0.32, None, None, 0.40, 0.43)


def canonical(value: Any) -> Any:
    """JSON-comparable form of a report: tuples as lists, floats rounded."""

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


def bounded_evidence_replay() -> list[dict[str, Any]]:
    """Present, update, dropout (empty and absent observation), expiry, reset."""

    def thing(thing_id: str, zone: str) -> dict[str, Any]:
        return {
            "thing_id": thing_id,
            "kind": "floor_boundary",
            "label": f"floor_boundary:{thing_id}",
            "confidence": 0.9,
            "location": {
                "frame": "image",
                "zone": zone,
                "bbox_xyxy_norm": [0.35, 0.45, 0.65, 0.95],
            },
            "source_plugin_id": "floor_plane",
        }

    def observation(frame_id: str, created_at_ms: int, things: tuple[dict, ...]) -> Observation:
        signals = (
            ({"signal_id": "floor_visible", "value": True, "confidence": 0.96},)
            if things
            else ()
        )
        return Observation(
            observation_id=frame_id,
            created_at_ms=created_at_ms,
            sensor_frame={},
            perception_plugin_id="lightweight_observer",
            summary=("replay",),
            things=things,
            signals=signals,
        )

    runner = MemoryRunner.from_activation(packaged_activation("memory"))
    shared_memory: dict[str, Any] = {}
    frames = (
        ("present_0", 100, observation("present_0", 100, (thing("a", "center"),))),
        ("present_1", 200, observation("present_1", 200, (thing("a", "left"), thing("b", "center")))),
        ("dropout_0", 300, observation("dropout_0", 300, ())),
        ("dropout_1", 400, None),
        ("expiry_0", 20_000, observation("expiry_0", 20_000, ())),
    )
    replay = []
    for index, (frame_id, timestamp_ms, frame_observation) in enumerate(frames):
        report = runner.update(
            DecisionFrameContext(frame_id, index, timestamp_ms, shared_memory=shared_memory),
            frame_observation,
        )
        replay.append({"frame_id": frame_id, "report": canonical(report)})
    runner.reset(shared_memory)
    replay.append({"frame_id": "reset", "report": canonical(runner.report())})
    return replay


def obstruction_image(left_edge: float | None) -> np.ndarray:
    """RGB frame: a bright textured-edge box on a dark background, or nothing."""

    rgb = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 60, dtype=np.uint8)
    if left_edge is not None:
        top_left = (int(left_edge * IMAGE_WIDTH), int(0.30 * IMAGE_HEIGHT))
        bottom_right = (int((left_edge + 0.22) * IMAGE_WIDTH), int(0.62 * IMAGE_HEIGHT))
        cv2.rectangle(rgb, top_left, bottom_right, (200, 200, 200), -1)
        cv2.rectangle(rgb, top_left, bottom_right, (255, 255, 255), 2)
    return rgb


class QuietDiagnostics(PerceptionDiagnosticSink):
    def __init__(self) -> None:
        super().__init__(output_dir=None, plugin_id="replay", allowed_artifacts=())


def tracks_frame_inputs(
    index: int,
    left_edge: float | None,
    shared_memory: dict[str, Any] | None,
) -> tuple[DecisionFrameContext, Observation]:
    """The context and observation the tracks memory plugin gets for one frame.

    The observation is what the perception ``multi_obstruction_tracks`` plugin
    emits for the image, the pairing the workbench e2e selects.
    """

    frame_id = f"frame_{index}"
    timestamp_ms = 100 + index * 100
    rgb = obstruction_image(left_edge)
    camera_frame = CameraFrame(
        "front_camera", timestamp_ms, np.ascontiguousarray(rgb), None, {"source_id": frame_id}
    )
    batch = MultiObstructionTracksPlugin().perceive(
        PerceptionPluginInputs(
            frame_id, timestamp_ms, {"frame": camera_frame}, QuietDiagnostics(),
            {"sequence_index": index}, shared_memory={},
        )
    )
    sensors = SensorFrame(
        read_id=frame_id,
        readings={
            FRONT_CAMERA_SENSOR_ID: SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                captured_at_ms=timestamp_ms,
                value=rgb,
                metadata={"color_space": "RGB"},
            )
        },
        started_at_ms=timestamp_ms,
        completed_at_ms=timestamp_ms,
    )
    context = DecisionFrameContext(
        frame_id=frame_id,
        frame_index=index,
        timestamp_ms=timestamp_ms,
        sensor_frame=sensors,
        shared_memory=shared_memory,
    )
    observation = Observation(
        observation_id=frame_id,
        created_at_ms=timestamp_ms,
        sensor_frame={},
        things=tuple(thing.to_dict() for thing in batch.things),
        signals=tuple(signal.to_dict() for signal in batch.signals),
    )
    return context, observation


def tracks_replay() -> list[dict[str, Any]]:
    """An obstruction appears, moves, leaves view for two frames, and returns.

    Each entry holds the memory report and the observation memory publishes in
    place of the perceived one (``decision.observation``): the tracked things
    and the track events.
    """

    runner = MemoryRunner.from_activation(
        packaged_activation("memory", ["multi_obstruction_tracks"])
    )
    shared_memory: dict[str, Any] = {}
    replay = []
    for index, left_edge in enumerate(OBSTRUCTION_LEFT_EDGES):
        context, observation = tracks_frame_inputs(index, left_edge, shared_memory)
        report = runner.update(context, observation)
        tracked = shared_memory["decision.observation"]
        replay.append(
            {
                "frame_id": context.frame_id,
                "report": canonical(report),
                "tracked_observation": canonical(
                    {
                        "things": [
                            {
                                "thing_id": thing["thing_id"],
                                "bbox_xyxy_norm": thing["location"]["bbox_xyxy_norm"],
                                "confidence": thing["confidence"],
                            }
                            for thing in tracked.things
                        ],
                        "track_events": {
                            str(track_id): event
                            for track_id, event in tracked.metadata["tracking"]["track_events"].items()
                        },
                    }
                ),
            }
        )
    return replay
