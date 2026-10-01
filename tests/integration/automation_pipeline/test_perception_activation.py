from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from autonomy.decision_cycle.activation import (
    read_step_activation,
    step_activation,
    write_step_activation,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.decision_cycle.catalog import step_plugins
from implementations.runtime.donkeycar import AutonomyPilotPart


class PerceptionActivationIntegrationTests(unittest.TestCase):
    def test_perception_activation_runs_on_in_memory_camera_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            write_step_activation(
                activation_path,
                step_activation("perception", ["frame"], {"frame": step_plugins("perception")["frame"]["spec"]}),
            )
            runner = PerceptionRunner.from_activation(
                read_step_activation(activation_path, "perception")
            )
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

            result = runner(
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
        self.assertEqual(runner.status()["plugin_ids"], ["frame"])
        self.assertEqual(runner.last_frame_index, 0)

        part = AutonomyPilotPart(
            host=AutonomyCycleHost(steps=replace(decision_steps(), perception=runner))
        )
        part.run(
            image_array=np.zeros((24, 32, 3), dtype=np.uint8),
            mode="local",
        )
        part.wait_for_cycle()
        _steering, _throttle, _control, _generation, cycle = part.completed_outputs("local")
        self.assertEqual(cycle["perception"]["status"], "ok")
        self.assertEqual(cycle["observation"]["perception_schema"], "perception_text_v2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
