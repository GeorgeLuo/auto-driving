from __future__ import annotations

from autonomy.perception import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionSignal,
    ViewLocation,
)
from implementations.perception.components import CameraFrame, FRONT_CAMERA_RGB_INPUT

from .frame_analysis import observe_rgb_frame


class FrameObservationPlugin:
    plugin_id = "frame-observation-v0"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        description="Report normalized frame dimensions and basic light statistics.",
        emits=(
            "signal front_camera_available",
            "thing front_camera_frame",
        ),
    )

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        measurements = observe_rgb_frame(frame.rgb)
        width = int(measurements["image_width_px"])
        height = int(measurements["image_height_px"])
        evidence = PerceivedThing(
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
                "brightness_mean": measurements["brightness_mean"],
                "contrast_std": measurements["contrast_std"],
            },
        )
        return PerceptionEvidenceBatch(
            signals=(PerceptionSignal("front_camera_available", True),),
            things=(evidence,),
            measurements=measurements,
        )
