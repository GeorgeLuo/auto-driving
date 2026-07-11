from __future__ import annotations

from pathlib import Path
from typing import Any

from autonomy.perception.core import observe_frame
from autonomy.perception.interface import PerceivedThing, PerceptionRequest, ViewLocation
from implementations.perception.chain import PerceptionPluginResult
from implementations.perception.text import thing_line
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID


class FrameObservationPlugin:
    plugin_id = "frame-observation-v0"

    def describe_schema(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "reads": ["front_camera image dimensions", "brightness_mean", "contrast_std"],
            "emits": ["frame line", "front_camera_frame thing"],
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionPluginResult:
        front = request.snapshot.readings.get(FRONT_CAMERA_SENSOR_ID)
        if front is None or front.path is None:
            return PerceptionPluginResult(
                lines=("signal id=front_camera_available value=false confidence=1.000",),
                observations={"frame": {"front_camera_available": False}},
                limits=("front camera image missing",),
            )

        image_path = Path(front.path)
        core = observe_frame(image_path)
        width = int(core["image_width_px"])
        height = int(core["image_height_px"])
        frame = PerceivedThing(
            thing_id="front_camera_frame",
            kind="sensor_frame",
            label="front camera frame",
            location=ViewLocation(
                frame="image",
                zone="full_frame",
                bbox_xyxy_norm=(0.0, 0.0, 1.0, 1.0),
            ),
            confidence=1.0,
            properties={
                "width_px": width,
                "height_px": height,
                "brightness_mean": core["brightness_mean"],
                "contrast_std": core["contrast_std"],
            },
        )
        return PerceptionPluginResult(
            lines=(
                "frame "
                f"width_px={width} height_px={height} "
                f"brightness_mean={core['brightness_mean']} contrast_std={core['contrast_std']}",
                thing_line(frame),
            ),
            things=(frame,),
            observations={"frame": core},
        )
