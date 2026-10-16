"""Associate the simulator's cardboard detections within one inspection run."""

from __future__ import annotations

import math
from typing import Any

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
from implementations.decision_cycle.perception.feeds.camera import FRONT_CAMERA_RGB_INPUT
from implementations.decision_cycle.perception.plugins.sim_cardboard_obstacle.plugin import (
    SimCardboardObstaclePlugin,
)
from implementations.decision_cycle.perception.shared.obstructions.boxes import center_distance, zone


class SimCardboardPermanencePlugin:
    """Keep bounded cardboard identities in host-owned, seedable history."""

    plugin_id = "sim_cardboard_permanence"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        memory_required=True,
        description="Associate simulator cardboard boxes and remember short detector gaps.",
        assumptions=(
            "the base detector identifies the simulator's orange-brown cardboard",
            "a cardboard object's image-space center moves gradually between frames",
        ),
        emits=(
            "signals obstacle_in_sight and cardboard_object_permanence",
            "one obstruction_evidence thing per observed or remembered object",
        ),
        limitations=(
            "identity is image-space and holds within one reset-bounded run",
            "nearby objects may be confused and large image-space jumps start new identities",
            "the base detector provides one box and no metric depth",
        ),
    )

    def __init__(
        self,
        *,
        max_center_shift: float = 0.25,
        max_missed_frames: int = 3,
        **detector_config: Any,
    ) -> None:
        self.max_center_shift = max(0.0, float(max_center_shift))
        self.max_missed_frames = max(0, int(max_missed_frames))
        self._detector = SimCardboardObstaclePlugin(**detector_config)

    @property
    def _memory_key(self) -> str:
        return f"perception.{self.plugin_id}.history"

    def reset(self, shared_memory=None) -> None:
        if shared_memory is not None:
            shared_memory.pop(self._memory_key, None)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        if inputs.shared_memory is None:
            raise ValueError(f"{self.plugin_id} requires host shared memory")
        detected = self._detector.perceive(inputs)
        obstacle = detected.measurements["obstacle"]
        objects, next_id = _restore_history(inputs.shared_memory.get(self._memory_key))
        objects_read = len(objects)
        bbox = obstacle["bbox_xyxy_norm"] if obstacle["present"] else None
        matched = None
        if bbox is not None:
            nearest = min(
                objects,
                key=lambda item: center_distance(item["bbox_xyxy_norm"], bbox),
                default=None,
            )
            if nearest is not None and center_distance(nearest["bbox_xyxy_norm"], bbox) <= self.max_center_shift:
                matched = nearest

        kept: list[dict[str, Any]] = []
        things: list[PerceivedThing] = []
        forgotten: list[int] = []
        for item in objects:
            if item is matched:
                item.update(
                    bbox_xyxy_norm=list(bbox),
                    seen_frames=item["seen_frames"] + 1,
                    missed_frames=0,
                )
                event = "matched"
            else:
                item["missed_frames"] += 1
                if item["missed_frames"] > self.max_missed_frames:
                    forgotten.append(item["object_id"])
                    continue
                event = "remembered"
            kept.append(item)
            things.append(_thing(item, event, obstacle["solidity"] if item is matched else None))

        if bbox is not None and matched is None:
            item = {
                "object_id": next_id,
                "bbox_xyxy_norm": list(bbox),
                "seen_frames": 1,
                "missed_frames": 0,
            }
            next_id += 1
            kept.append(item)
            things.append(_thing(item, "new", obstacle["solidity"]))

        inputs.shared_memory[self._memory_key] = {"objects": kept, "next_object_id": next_id}
        return PerceptionEvidenceBatch(
            signals=(
                *detected.signals,
                PerceptionSignal(
                    signal_id="cardboard_object_permanence",
                    value=len(kept),
                    properties={"objects_read": objects_read, "forgotten": forgotten},
                ),
            ),
            things=tuple(things),
            measurements=detected.measurements,
        )


def _restore_history(history: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(history, dict):
        return [], 1
    objects: list[dict[str, Any]] = []
    ids: set[int] = set()
    recorded = history.get("objects")
    for item in recorded if isinstance(recorded, list) else ():
        if not isinstance(item, dict):
            continue
        object_id = item.get("object_id")
        bbox = item.get("bbox_xyxy_norm")
        seen = item.get("seen_frames")
        missed = item.get("missed_frames")
        if (
            type(object_id) is not int or object_id <= 0 or object_id in ids
            or not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox)
            or type(seen) is not int or seen < 0
            or type(missed) is not int or missed < 0
        ):
            continue
        ids.add(object_id)
        objects.append({
            "object_id": object_id,
            "bbox_xyxy_norm": list(bbox),
            "seen_frames": seen,
            "missed_frames": missed,
        })
    try:
        next_id = int(history.get("next_object_id", 1))
    except (TypeError, ValueError, OverflowError):
        next_id = 1
    return objects, max(next_id, max(ids, default=0) + 1)


def _thing(item: dict[str, Any], event: str, confidence: float | None) -> PerceivedThing:
    bbox = tuple(item["bbox_xyxy_norm"])
    return PerceivedThing(
        thing_id=f"cardboard_object_{item['object_id']:03d}",
        kind="obstruction_evidence",
        label="cardboard obstacle",
        location=ViewLocation(frame="image", zone=zone(bbox), bbox_xyxy_norm=bbox),
        confidence=confidence,
        properties={
            "object_id": item["object_id"],
            "event": event,
            "seen_frames": item["seen_frames"],
            "missed_frames": item["missed_frames"],
        },
    )
