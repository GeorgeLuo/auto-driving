from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.perception.activation import (
    ActivatedPerceptionStep,
    read_perception_activation,
)
from autonomy.runtime import AutonomyManager
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.decision_cycle.perception.catalog import (
    PERCEPTION_MAPPER_SPEC,
    PERCEPTION_PLUGIN_SPECS,
)
from implementations.runtime.donkeycar import AutonomyPilotPart


class PerceptionActivationIntegrationTests(unittest.TestCase):
    def test_perception_activation_runs_on_in_memory_camera_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            activation_path.write_text(
                json.dumps(
                    {
                        "schema": "automa_perception_activation_v0",
                        "perception": {
                            "algorithm": "test-observer",
                            "mapper_spec": PERCEPTION_MAPPER_SPEC,
                            "mapper_config": {
                                "plugins": ["frame"],
                                "plugin_specs": {"frame": PERCEPTION_PLUGIN_SPECS["frame"]},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            step = ActivatedPerceptionStep(read_perception_activation(activation_path))
            snapshot = SensorSnapshot(
                read_id="onboard-frame",
                readings={
                    FRONT_CAMERA_SENSOR_ID: SensorReading(
                        sensor_id=FRONT_CAMERA_SENSOR_ID,
                        sensor_kind="camera",
                        captured_at_ms=10,
                        value=np.zeros((24, 32, 3), dtype=np.uint8),
                    )
                },
                started_at_ms=10,
                completed_at_ms=10,
            )

            result = step(
                DecisionFrameContext(
                    frame_id="onboard-frame",
                    frame_index=0,
                    timestamp_ms=10,
                    sensor_snapshot=snapshot,
                )
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.artifacts, {})
        step_status = step.status()
        self.assertEqual(step_status["algorithm"], "test-observer")
        self.assertEqual(step_status["last_status"], "ok")
        self.assertEqual(step_status["last_frame_index"], 0)
        self.assertEqual(step_status["last_thing_count"], 1)
        self.assertGreaterEqual(step_status["last_duration_ms"], 0.0)
        self.assertEqual(
            [run["plugin_id"] for run in step_status["last_plugin_runs"]],
            ["frame"],
        )
        self.assertEqual(
            step_status["last_plugin_runs"][0]["implementation_id"],
            "frame-observation-v0",
        )
        self.assertEqual(step.mapper.plugins[0].plugin_id, "frame-observation-v0")

        manager = AutonomyManager()
        manager.register_status_provider("perception", step.status)
        self.assertEqual(
            manager.status()["components"]["perception"]["algorithm"],
            "test-observer",
        )

        part = AutonomyPilotPart(
            host=AutonomyCycleHost(steps=DecisionSteps(perceive=step))
        )
        part.run(
            image_array=np.zeros((24, 32, 3), dtype=np.uint8),
            mode="local",
        )
        part.wait_for_cycle()
        _steering, _throttle, _control, _engine, cycle = part.completed_outputs("local")
        self.assertEqual(cycle["perception"]["status"], "ok")
        self.assertEqual(cycle["observation"]["perception_schema"], "perception_text_v2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
