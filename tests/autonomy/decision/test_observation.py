from __future__ import annotations

import json
import unittest

from autonomy.decision import OBSERVATION_SCHEMA, observation_from_perception
from autonomy.perception import (
    PERCEPTION_TEXT_SCHEMA,
    PerceivedThing,
    PerceptionSignal,
    PerceptionText,
    ViewLocation,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot


class ObservationAdaptationTests(unittest.TestCase):
    def test_perception_becomes_inspectable_decision_observation(self) -> None:
        sensor_snapshot = SensorSnapshot(
            read_id="frame_007",
            readings={
                FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=700,
                    value=object(),
                    metadata={"capture": {"source": "test-camera"}},
                )
            },
            started_at_ms=699,
            completed_at_ms=700,
        )
        perception = PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test-perception",
            status="ok",
            lines=tuple(f"evidence line {index}" for index in range(15)),
            signals=(
                PerceptionSignal(
                    signal_id="path_clear",
                    value=True,
                    confidence=0.9,
                ),
            ),
            things=(
                PerceivedThing(
                    thing_id="region_1",
                    kind="region_proposal",
                    label="region",
                    location=ViewLocation(
                        frame="image",
                        zone="center",
                        bbox_xyxy_norm=(0.2, 0.3, 0.7, 0.9),
                    ),
                    confidence=0.8,
                    properties={"appearance": {"color": "red"}},
                ),
            ),
            artifacts={"overlay": "artifacts/overlay.png"},
            limits=("relative image location only",),
        )

        observation = observation_from_perception(
            observation_id="frame_007",
            sensor_snapshot=sensor_snapshot,
            perception=perception,
            metadata={"source": "test-observe-stage"},
            created_at_ms=0,
        )

        self.assertEqual(observation.schema, OBSERVATION_SCHEMA)
        self.assertEqual(observation.created_at_ms, 0)
        self.assertEqual(observation.perception_schema, PERCEPTION_TEXT_SCHEMA)
        self.assertEqual(observation.perception_plugin_id, "test-perception")
        self.assertEqual(observation.summary, perception.lines[:12])
        self.assertEqual(observation.signals[0]["signal_id"], "path_clear")
        self.assertEqual(observation.things[0]["thing_id"], "region_1")
        self.assertEqual(observation.artifacts["overlay"], "artifacts/overlay.png")
        self.assertEqual(observation.metadata["limits"], list(perception.limits))
        self.assertEqual(observation.metadata["source"], "test-observe-stage")

        camera = observation.sensor_snapshot["readings"][FRONT_CAMERA_SENSOR_ID]
        self.assertTrue(camera["has_value"])
        self.assertNotIn("value", camera)

        serialized = observation.to_dict()
        json.dumps(serialized)
        serialized["things"][0]["properties"]["appearance"]["color"] = "blue"
        serialized["sensor_snapshot"]["readings"][FRONT_CAMERA_SENSOR_ID]["metadata"][
            "capture"
        ]["source"] = "changed-camera"

        self.assertEqual(
            observation.things[0]["properties"]["appearance"]["color"],
            "red",
        )
        self.assertEqual(
            observation.sensor_snapshot["readings"][FRONT_CAMERA_SENSOR_ID]["metadata"][
                "capture"
            ]["source"],
            "test-camera",
        )

    def test_missing_perception_produces_explicit_empty_observation(self) -> None:
        observation = observation_from_perception(
            observation_id="frame_008",
            sensor_snapshot=None,
            perception=None,
            metadata={"source": "manual-observe-stage"},
            created_at_ms=800,
        )

        self.assertEqual(observation.sensor_snapshot, {})
        self.assertIsNone(observation.perception_schema)
        self.assertIsNone(observation.perception_plugin_id)
        self.assertEqual(
            observation.summary,
            ("observation_available=false reason=no_perception",),
        )
        self.assertEqual(observation.metadata["source"], "manual-observe-stage")


if __name__ == "__main__":
    unittest.main(verbosity=2)
