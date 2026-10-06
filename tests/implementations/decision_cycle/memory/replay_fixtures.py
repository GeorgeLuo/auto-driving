"""Scripted replays of the packaged memory plugins, and their pinned reports.

Each replay drives a ``MemoryRunner`` over fixed inputs and returns the
``memory_report_v1`` after every frame. The reports are pinned in ``baselines/``
so a change that should not alter what memory publishes (a move, a rename)
shows as an unchanged report; a change that does alter it edits the baseline in
the same commit. Floats are rounded to four decimals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.catalog import packaged_activation


BASELINES = Path(__file__).with_name("baselines")


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
