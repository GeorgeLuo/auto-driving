from __future__ import annotations

from copy import copy, deepcopy
from typing import Any

import numpy as np

from autonomy.perception import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionSignal,
    ViewLocation,
)
from implementations.perception.components import CameraFrame, FRONT_CAMERA_RGB_INPUT
from lab.plugins.perception.floor_continuity.src.plugin import FloorContinuityPlugin


class TemporalFloorContinuityPlugin:
    """Emit one temporally associated floor interruption per frame.

    The underlying detector remains the existing stateless floor-continuity
    cue. This wrapper makes the decision-facing representation bounded: one
    candidate is associated with the previous candidate, its box is smoothed,
    and a short detector miss may reuse the last geometry with decayed
    confidence. It does not claim object identity or metric depth.
    """

    plugin_id = "floor-continuity-temporal-v0"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        memory_required=True,
        description=(
            "Associate one floor-continuity interruption across adjacent frames "
            "and smooth its image-space geometry before decision use."
        ),
        assumptions=(
            "the decision-important interruption moves gradually between adjacent frames",
            "the base floor-continuity detector supplies at least one useful candidate",
            "short detector gaps are safer to bridge than to turn into a new lateral cue",
        ),
        emits=(
            "signals floor_visible and temporal_floor_boundary_available",
            "one image-space temporally smoothed floor_boundary record",
        ),
        limitations=(
            "association is local to one reset-bounded run",
            "held geometry is evidence continuity, not object identity",
            "smoothing can lag a genuine obstacle crossing or suppress a new obstacle",
            "the base cue remains heuristic and does not establish traversability",
        ),
        diagnostic_artifacts=(
            "floor_mask",
            "boundary_mask",
            "overlay",
            "summary",
            "temporal_summary",
        ),
    )

    def __init__(
        self,
        *,
        smoothing_alpha: float = 0.45,
        association_distance: float = 0.30,
        minimum_association_score: float = 0.18,
        max_hold_frames: int = 2,
        **base_config: Any,
    ) -> None:
        self.smoothing_alpha = _clamp(float(smoothing_alpha), 0.05, 1.0)
        self.association_distance = max(0.01, float(association_distance))
        self.minimum_association_score = _clamp(
            float(minimum_association_score), 0.0, 1.0
        )
        self.max_hold_frames = max(0, int(max_hold_frames))
        self._base = FloorContinuityPlugin(**base_config)

    def _initialize_history(self) -> None:
        self._last_bbox: tuple[float, float, float, float] | None = None
        self._last_confidence = 0.0
        self._misses = 0
        self._age = 0

    @property
    def _memory_key(self) -> str:
        return f"perception.{self.plugin_id}.history"

    def reset(self, memory=None) -> None:
        if memory is not None:
            memory.pop(self._memory_key, None)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        if inputs.memory is None:
            raise ValueError(f"{self.plugin_id} requires host shared memory")
        step = copy(self)
        step._initialize_history()
        for name, value in deepcopy(inputs.memory.get(self._memory_key, {})).items():
            setattr(step, name, value)
        batch = step._perceive_frame(inputs)
        inputs.memory[self._memory_key] = {
            name: getattr(step, name)
            for name in ("_last_bbox", "_last_confidence", "_misses", "_age")
        }
        return batch

    def _perceive_frame(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        base = self._base.perceive(inputs)
        raw = [thing for thing in base.things if thing.kind == "floor_boundary"]
        selected, association = self._select(raw)

        things: list[PerceivedThing] = [
            thing for thing in base.things if thing.kind != "floor_boundary"
        ]
        if selected is not None:
            things.append(selected)

        signals: list[PerceptionSignal] = []
        for signal in base.signals:
            if signal.signal_id == "floor_boundary_available":
                continue
            signals.append(signal)
        signals.append(
            PerceptionSignal(
                "temporal_floor_boundary_available",
                selected is not None,
                selected.confidence if selected is not None else 0.0,
                {
                    "raw_boundary_count": len(raw),
                    "selected": selected is not None,
                    "held": bool(selected and selected.properties.get("held")),
                    "age_frames": self._age,
                    "missed_frames": self._misses,
                },
            )
        )

        measurements = dict(base.measurements)
        measurements.update(
            {
                "raw_boundary_count": len(raw),
                "temporal_boundary_count": int(selected is not None),
                "temporal_boundary": (
                    selected.location.bbox_xyxy_norm if selected is not None else None
                ),
                "temporal_boundary_confidence": (
                    selected.confidence if selected is not None else 0.0
                ),
                "association": association,
                "smoothing_alpha": self.smoothing_alpha,
                "max_hold_frames": self.max_hold_frames,
            }
        )
        if inputs.diagnostics.enabled:
            inputs.diagnostics.emit_json(
                "temporal_summary",
                "temporal_summary.json",
                {
                    "frame_id": inputs.frame_id,
                    "raw_boundary_count": len(raw),
                    "selected": selected.to_dict() if selected is not None else None,
                    "association": association,
                    "age_frames": self._age,
                    "missed_frames": self._misses,
                },
            )
        return PerceptionEvidenceBatch(
            signals=tuple(signals),
            things=tuple(things),
            measurements=measurements,
        )

    def _select(
        self,
        raw: list[PerceivedThing],
    ) -> tuple[PerceivedThing | None, dict[str, Any]]:
        if not raw:
            self._misses += 1
            if self._last_bbox is not None and self._misses <= self.max_hold_frames:
                held = _materialize(
                    self._last_bbox,
                    self._last_confidence * (0.82**self._misses),
                    age_frames=self._age,
                    missed_frames=self._misses,
                    held=True,
                )
                return held, {
                    "matched": False,
                    "held": True,
                    "score": 0.0,
                    "reason": "short_detector_gap",
                }
            self._last_bbox = None
            self._last_confidence = 0.0
            self._age = 0
            return None, {
                "matched": False,
                "held": False,
                "score": 0.0,
                "reason": "no_candidate",
            }

        if self._last_bbox is None:
            candidate = max(raw, key=_candidate_priority)
            score = 1.0
            reason = "initial_highest_confidence"
            matched = False
        else:
            scored = [
                (_association_score(self._last_bbox, candidate.location.bbox_xyxy_norm, candidate.confidence, self.association_distance), candidate)
                for candidate in raw
                if candidate.location.bbox_xyxy_norm is not None
            ]
            score, candidate = max(scored, key=lambda item: item[0], default=(0.0, None))
            matched = candidate is not None and score >= self.minimum_association_score
            if not matched:
                candidate = max(raw, key=_candidate_priority)
                score = 0.0
                reason = "association_restarted"
            else:
                reason = "associated"

        assert candidate is not None
        bbox = candidate.location.bbox_xyxy_norm
        if bbox is None:
            return None, {"matched": False, "held": False, "score": 0.0, "reason": "missing_bbox"}
        if self._last_bbox is None or not matched:
            smoothed = tuple(float(value) for value in bbox)
        else:
            alpha = self.smoothing_alpha
            smoothed = tuple(
                (1.0 - alpha) * old + alpha * new
                for old, new in zip(self._last_bbox, bbox, strict=True)
            )
        self._last_bbox = smoothed
        self._last_confidence = (
            float(candidate.confidence)
            if self._age == 0 or not matched
            else (1.0 - self.smoothing_alpha) * self._last_confidence
            + self.smoothing_alpha * float(candidate.confidence)
        )
        self._misses = 0
        self._age += 1
        return _materialize(
            smoothed,
            self._last_confidence,
            age_frames=self._age,
            missed_frames=0,
            held=False,
            source=candidate,
        ), {
            "matched": matched,
            "held": False,
            "score": round(float(score), 5),
            "reason": reason,
        }


def _materialize(
    bbox: tuple[float, float, float, float],
    confidence: float,
    *,
    age_frames: int,
    missed_frames: int,
    held: bool,
    source: PerceivedThing | None = None,
) -> PerceivedThing:
    properties = dict(source.properties if source is not None else {})
    properties.update(
        {
            "evidence": "temporal_floor_continuity_interruption",
            "temporal_age_frames": age_frames,
            "temporal_missed_frames": missed_frames,
            "held": held,
        }
    )
    return PerceivedThing(
        thing_id="temporal_floor_boundary_000",
        kind="floor_boundary",
        label="temporally associated supported floor interruption",
        location=ViewLocation(
            frame="image",
            zone=_zone(bbox),
            bbox_xyxy_norm=bbox,
        ),
        confidence=_clamp(confidence, 0.0, 1.0),
        properties=properties,
    )


def _candidate_priority(thing: PerceivedThing) -> tuple[float, float, float]:
    bbox = thing.location.bbox_xyxy_norm or (0.0, 0.0, 0.0, 0.0)
    return (
        float(thing.confidence),
        max(0.0, bbox[3] - bbox[1]),
        max(0.0, bbox[2] - bbox[0]),
    )


def _association_score(
    previous: tuple[float, float, float, float],
    current: tuple[float, float, float, float] | None,
    confidence: float,
    max_distance: float,
) -> float:
    if current is None:
        return 0.0
    iou = _iou(previous, current)
    old_center = ((previous[0] + previous[2]) / 2.0, (previous[1] + previous[3]) / 2.0)
    new_center = ((current[0] + current[2]) / 2.0, (current[1] + current[3]) / 2.0)
    distance = float(np.hypot(old_center[0] - new_center[0], old_center[1] - new_center[1]))
    distance_score = max(0.0, 1.0 - distance / max(max_distance, 1e-6))
    return float(0.55 * iou + 0.30 * distance_score + 0.15 * _clamp(confidence, 0.0, 1.0))


def _iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _zone(bbox: tuple[float, float, float, float]) -> str:
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    horizontal = "left" if cx < 0.4 else "right" if cx > 0.6 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
