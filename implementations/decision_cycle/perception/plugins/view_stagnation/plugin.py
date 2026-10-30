"""Measure sustained visual similarity without inferring vehicle motion."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image

from autonomy.decision_cycle.perception.evidence.values import (
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
)
from implementations.decision_cycle.perception.feeds.camera import (
    CameraFrame,
    FRONT_CAMERA_RGB_INPUT,
)
from implementations.decision_cycle.perception.shared.image.stored import StoredImage


RESULT_KEY = "perception.view_stagnation"
HISTORY_KEY = f"{RESULT_KEY}.history"


class ViewStagnationPlugin:
    """Compare a bounded thumbnail against the start of an unchanged interval."""

    plugin_id = "view_stagnation"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        memory_required=True,
        description="Report sustained low visual change over fresh camera frames.",
        assumptions=(
            "camera capture timestamps increase within one stream",
            "frame ids identify distinct captures",
        ),
        emits=("signal view_static",),
        limitations=(
            "visual similarity does not establish that the car is stuck",
            "weak texture or very slow movement can resemble a static view",
            "lighting changes, camera shake and moving objects can interrupt stagnation",
            "fresh ids and timestamps cannot prove a camera is not replaying pixels",
        ),
    )

    def __init__(
        self,
        *,
        thumbnail_width: int = 64,
        thumbnail_height: int = 48,
        change_threshold: float = 0.01,
        static_after_ms: int = 1000,
        min_samples: int = 3,
        max_gap_ms: int = 2000,
    ) -> None:
        for name, value, low, high in (
            ("thumbnail_width", thumbnail_width, 8, 256),
            ("thumbnail_height", thumbnail_height, 8, 256),
            ("static_after_ms", static_after_ms, 1, 60000),
            ("min_samples", min_samples, 2, 10000),
            ("max_gap_ms", max_gap_ms, 1, 60000),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in {low}..{high}")
        if (
            isinstance(change_threshold, bool)
            or not isinstance(change_threshold, (int, float))
            or not math.isfinite(change_threshold)
            or not 0 < change_threshold < 1
        ):
            raise ValueError("change_threshold must be finite and between 0 and 1")
        self.thumbnail_width = thumbnail_width
        self.thumbnail_height = thumbnail_height
        self.change_threshold = float(change_threshold)
        self.static_after_ms = static_after_ms
        self.min_samples = min_samples
        self.max_gap_ms = max_gap_ms

    def reset(self, shared_memory=None) -> None:
        if shared_memory is not None:
            shared_memory.pop(HISTORY_KEY, None)
            shared_memory[RESULT_KEY] = _result(None, None, "unknown", "reset")

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        memory = inputs.shared_memory
        if memory is None:
            raise ValueError(f"{self.plugin_id} requires host shared memory")
        # Invalidate the old publication before processing, including on error.
        memory[RESULT_KEY] = _result(inputs.frame_id, None, "unknown", "processing")
        frame = inputs.require("frame", CameraFrame)
        captured_at_ms = frame.captured_at_ms
        if type(captured_at_ms) is not int or captured_at_ms < 0:
            memory.pop(HISTORY_KEY, None)
            return self._publish(inputs, _result(inputs.frame_id, None, "unknown", "invalid_timestamp"))

        signature = [
            frame.sensor_id, frame.width_px, frame.height_px,
            self.thumbnail_width, self.thumbnail_height, self.change_threshold,
            self.static_after_ms, self.min_samples, self.max_gap_ms,
        ]
        history = _history(memory.get(HISTORY_KEY), signature)
        if history is not None and (
            history["frame_id"] == inputs.frame_id
            or captured_at_ms <= history["captured_at_ms"]
        ):
            memory.pop(HISTORY_KEY, None)
            return self._publish(inputs, _result(
                inputs.frame_id, captured_at_ms, "unknown", "non_fresh_frame",
            ))

        thumbnail = np.array(Image.fromarray(frame.rgb).convert("L").resize(
            (self.thumbnail_width, self.thumbnail_height), Image.Resampling.BOX,
        ), dtype=np.uint8)
        reason = "first_frame"
        if history is not None and captured_at_ms - history["captured_at_ms"] > self.max_gap_ms:
            history = None
            reason = "capture_gap"

        change_score = None
        status = "warming_up"
        anchor = thumbnail
        anchor_at_ms = captured_at_ms
        samples = 1
        if history is not None:
            previous_anchor = history["anchor"].pixels
            change_score = float(np.abs(
                thumbnail.astype(np.float32) - previous_anchor.astype(np.float32),
            ).mean() / 255.0)
            if change_score > self.change_threshold:
                status, reason = "moving", "visual_change"
            else:
                anchor = previous_anchor
                anchor_at_ms = history["anchor_at_ms"]
                samples = history["sample_count"] + 1
                reason = "insufficient_history"
                if captured_at_ms - anchor_at_ms >= self.static_after_ms and samples >= self.min_samples:
                    status, reason = "static", "low_visual_change"

        result = _result(inputs.frame_id, captured_at_ms, status, reason)
        result.update(
            change_score=change_score,
            unchanged_for_ms=captured_at_ms - anchor_at_ms,
            sample_count=samples,
        )
        if status == "static":
            result["confidence"] = max(0.0, 1.0 - change_score / self.change_threshold)
        elif status == "moving":
            result["confidence"] = max(0.0, 1.0 - self.change_threshold / change_score)
        memory[HISTORY_KEY] = {
            "signature": signature,
            "anchor": StoredImage(anchor),
            "anchor_at_ms": anchor_at_ms,
            "captured_at_ms": captured_at_ms,
            "frame_id": inputs.frame_id,
            "sample_count": samples,
            "result": dict(result),
        }
        return self._publish(inputs, result)

    def _publish(self, inputs: PerceptionPluginInputs, result: dict[str, Any]) -> PerceptionEvidenceBatch:
        inputs.shared_memory[RESULT_KEY] = dict(result)
        return PerceptionEvidenceBatch(signals=(PerceptionSignal(
            signal_id="view_static",
            value=result["view_static"],
            confidence=result["confidence"],
            properties={key: value for key, value in result.items() if key not in {"view_static", "confidence"}},
        ),))


def _result(frame_id, captured_at_ms, status: str, reason: str) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "captured_at_ms": captured_at_ms,
        "status": status,
        "reason": reason,
        "view_static": True if status == "static" else False if status == "moving" else None,
        "change_score": None,
        "unchanged_for_ms": 0,
        "sample_count": 0,
        "confidence": None,
    }


def _history(value: Any, signature: list[Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("signature") != signature:
        return None
    anchor = value.get("anchor")
    if (
        not isinstance(anchor, StoredImage)
        or anchor.pixels.dtype != np.uint8
        or anchor.pixels.shape != (signature[4], signature[3])
        or not isinstance(value.get("frame_id"), str)
    ):
        return None
    if any(type(value.get(key)) is not int for key in ("captured_at_ms", "anchor_at_ms", "sample_count")):
        return None
    if not 0 <= value["anchor_at_ms"] <= value["captured_at_ms"] or value["sample_count"] < 1:
        return None
    return value
