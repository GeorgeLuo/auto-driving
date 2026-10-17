"""Map this frame's cardboard detections to host-owned object identities."""

from __future__ import annotations

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
from implementations.decision_cycle.perception.shared.obstructions.boxes import center_distance
from implementations.decision_cycle.perception.shared.serialization.history import (
    history_bbox,
    history_int,
    next_history_id,
)


class SimCardboardPermanencePlugin:
    """Keep bounded cardboard identities in host-owned, seedable history."""

    plugin_id = "sim_cardboard_permanence"
    contract = PerceptionPluginContract(
        inputs=(),
        state_mode="windowed",
        memory_required=True,
        description="Map each earlier cardboard detection to a distinct nearby known object.",
        assumptions=(
            "the detection plugin runs earlier in the same selection",
            "a known object's image-space center moves gradually between sightings",
        ),
        emits=(
            "signal cardboard_object_permanence",
            "one obstruction_evidence thing per cardboard detection visible this frame",
        ),
        limitations=(
            "identity is image-space and holds within one reset-bounded run",
            "nearby objects may be confused and large image-space jumps start new identities",
            "a gap longer than max_missed_frames starts a new identity",
            "without the detection plugin there is nothing to map",
        ),
    )

    def __init__(
        self,
        *,
        detection_plugin_id: str = "sim_cardboard_obstacle",
        max_center_shift: float = 0.25,
        max_missed_frames: int = 3,
    ) -> None:
        self.detection_plugin_id = str(detection_plugin_id)
        self.max_center_shift = max(0.0, float(max_center_shift))
        self.max_missed_frames = max(0, int(max_missed_frames))

    @property
    def _memory_key(self) -> str:
        return f"perception.{self.plugin_id}.history"

    def reset(self, shared_memory=None) -> None:
        if shared_memory is not None:
            shared_memory.pop(self._memory_key, None)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        if inputs.shared_memory is None:
            raise ValueError(f"{self.plugin_id} requires host shared memory")
        detections = _detections(inputs.prior_things, self.detection_plugin_id)
        objects, next_id = _restore_history(inputs.shared_memory.get(self._memory_key))
        objects_read = len(objects)
        matched = _match(
            [item for item in objects if item["missed_frames"] <= self.max_missed_frames],
            detections,
            self.max_center_shift,
        )

        things: list[PerceivedThing] = []
        observed_ids: set[int] = set()
        new_objects: list[dict[str, Any]] = []
        for index, detection in enumerate(detections):
            bbox = detection.location.bbox_xyxy_norm
            item = matched.get(index)
            if item is not None:
                item.update(
                    bbox_xyxy_norm=list(bbox),
                    seen_frames=item["seen_frames"] + 1,
                    missed_frames=0,
                )
                event = "matched"
            else:
                item = {
                    "object_id": next_id,
                    "bbox_xyxy_norm": list(bbox),
                    "seen_frames": 1,
                    "missed_frames": 0,
                }
                next_id += 1
                new_objects.append(item)
                event = "new"
            observed_ids.add(item["object_id"])
            things.append(_thing(item, event, detection))

        kept: list[dict[str, Any]] = []
        forgotten: list[int] = []
        for item in objects:
            if item["object_id"] in observed_ids:
                kept.append(item)
                continue
            item["missed_frames"] += 1
            if item["missed_frames"] > self.max_missed_frames:
                forgotten.append(item["object_id"])
                continue
            kept.append(item)

        kept.extend(new_objects)

        inputs.shared_memory[self._memory_key] = {"objects": kept, "next_object_id": next_id}
        return PerceptionEvidenceBatch(
            signals=(
                PerceptionSignal(
                    signal_id="cardboard_object_permanence",
                    value=len(kept),
                    properties={"objects_read": objects_read, "forgotten": forgotten},
                ),
            ),
            things=tuple(things),
        )


def _detections(things: tuple[PerceivedThing, ...], plugin_id: str) -> tuple[PerceivedThing, ...]:
    """All current cardboard detections, with their location and confidence."""

    return tuple(
        thing for thing in things
        if thing.source_plugin_id == plugin_id
        and thing.kind == "obstruction_evidence"
        and thing.location.bbox_xyxy_norm is not None
    )


def _match(
    objects: list[dict[str, Any]],
    detections: tuple[PerceivedThing, ...],
    max_center_shift: float,
) -> dict[int, dict[str, Any]]:
    """Assign the nearest valid pairs, using each detection and identity once."""

    pairs = []
    for object_index, item in enumerate(objects):
        for detection_index, detection in enumerate(detections):
            distance = center_distance(item["bbox_xyxy_norm"], detection.location.bbox_xyxy_norm)
            if distance <= max_center_shift:
                pairs.append((distance, object_index, detection_index))
    matches: dict[int, dict[str, Any]] = {}
    used_objects: set[int] = set()
    for _distance, object_index, detection_index in sorted(pairs):
        if object_index in used_objects or detection_index in matches:
            continue
        used_objects.add(object_index)
        matches[detection_index] = objects[object_index]
    return matches


def _restore_history(history: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(history, dict):
        return [], 1
    objects: list[dict[str, Any]] = []
    ids: set[int] = set()
    recorded = history.get("objects")
    for item in recorded if isinstance(recorded, list) else ():
        if not isinstance(item, dict):
            continue
        object_id = history_int(item.get("object_id"))
        bbox = history_bbox(item.get("bbox_xyxy_norm"))
        seen = history_int(item.get("seen_frames"))
        missed = history_int(item.get("missed_frames"))
        if (
            object_id is None or object_id <= 0 or object_id in ids or bbox is None
            or seen is None or seen < 0 or missed is None or missed < 0
        ):
            continue
        ids.add(object_id)
        objects.append({
            "object_id": object_id,
            "bbox_xyxy_norm": list(bbox),
            "seen_frames": seen,
            "missed_frames": missed,
        })
    return objects, next_history_id(history.get("next_object_id"), ids)


def _label(object_id: int, event: str) -> str:
    if event == "new":
        return "new cardboard"
    return f"known cardboard {object_id:03d}"


def _thing(item: dict[str, Any], event: str, detection: PerceivedThing) -> PerceivedThing:
    return PerceivedThing(
        thing_id=f"cardboard_object_{item['object_id']:03d}",
        kind="obstruction_evidence",
        label=_label(item["object_id"], event),
        location=detection.location,
        confidence=detection.confidence,
        properties={
            "object_id": item["object_id"],
            "event": event,
            "seen_frames": item["seen_frames"],
            "missed_frames": item["missed_frames"],
        },
    )
