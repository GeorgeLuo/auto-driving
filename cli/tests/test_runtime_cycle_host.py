from __future__ import annotations

import unittest

from autonomy.decision import DecisionFrameContext, DecisionStages
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.runtime.donkeycar import AutonomyPilotPart


class RuntimeCycleHostTests(unittest.TestCase):
    def test_host_runs_engine_with_in_memory_front_camera_value(self) -> None:
        image_value = object()
        sensor_snapshot = SensorSnapshot(
            read_id="frame_000",
            readings={
                FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=100,
                    value=image_value,
                )
            },
            started_at_ms=100,
            completed_at_ms=100,
        )
        host = AutonomyCycleHost()

        result = host.run(
            DecisionFrameContext(
                frame_id="frame_000",
                frame_index=0,
                timestamp_ms=100,
                sensor_snapshot=sensor_snapshot,
            )
        )

        self.assertEqual(result.control.reason, "stable-idle-engine")
        self.assertTrue(result.control.metadata["has_sensor_snapshot"])
        self.assertEqual(host.manager.status()["step_count"], 1)
        self.assertTrue(result.to_dict()["context"]["sensor_snapshot"]["readings"][FRONT_CAMERA_SENSOR_ID]["has_value"])

    def test_host_rejects_a_second_action_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "owns the decision action stage"):
            AutonomyCycleHost(stages=DecisionStages(choose_action=lambda *args: None))

    def test_donkey_part_returns_the_shared_cycle_shape(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost())

        steering, throttle, control, engine, cycle = part.run(
            image_array=object(),
            mode="local",
        )

        self.assertEqual(steering, 0.0)
        self.assertEqual(throttle, 0.0)
        self.assertEqual(control["reason"], "stable-idle-engine")
        self.assertEqual(engine, "autonomy.runtime.engine:IdleAutonomyEngine")
        self.assertEqual(cycle["schema"], "decision_cycle_result_v0")
        self.assertEqual(cycle["context"]["frame_id"], "donkey_frame_000000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
