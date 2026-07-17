from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from autonomy.decision import DecisionFrameContext, DecisionStages
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.engine import AutonomyControl, AutonomySnapshot
from autonomy.runtime.manager import AutonomyManager
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.runtime.donkeycar import (
    DEFAULT_OBSERVATION_INTERVAL_S,
    AutonomyPilotPart,
    ONBOARD_OBSERVATION_SNAPSHOT_SCHEMA,
)


class _Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class _PushyEngine:
    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": "pushy-test",
            "engine_spec": "tests:_PushyEngine",
        }

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        del snapshot
        return AutonomyControl(
            steering=0.7,
            throttle=0.4,
            confidence=1.0,
            reason="pushy-test-engine",
        )


class _ExplodingHost:
    def status(self) -> dict:
        return {"engine": {"engine": "exploding-host"}, "last_cycle": None}

    def run(self, context: DecisionFrameContext):
        del context
        raise RuntimeError("cycle failed")


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
        self.assertTrue(
            result.to_dict()["context"]["sensor_snapshot"]["readings"][FRONT_CAMERA_SENSOR_ID][
                "has_value"
            ]
        )

    def test_host_rejects_a_second_action_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "owns the decision action stage"):
            AutonomyCycleHost(stages=DecisionStages(choose_action=lambda *args: None))

    def test_donkey_part_returns_the_shared_cycle_shape(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost(), min_interval_s=0.0)

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

    def test_manual_mode_runs_cycle_and_forces_zero_pilot_outputs(self) -> None:
        manager = AutonomyManager()
        manager.engine = _PushyEngine()
        part = AutonomyPilotPart(
            host=AutonomyCycleHost(manager=manager),
            min_interval_s=0.0,
        )

        steering, throttle, control, _engine, cycle = part.run(
            image_array=np.zeros((4, 4, 3), dtype=np.uint8),
            mode="user",
            user_steering=0.2,
            user_throttle=0.1,
        )

        self.assertEqual(steering, 0.0)
        self.assertEqual(throttle, 0.0)
        self.assertEqual(control["reason"], "pushy-test-engine")
        self.assertEqual(control["steering"], 0.7)
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle["context"]["mode"], "user")
        self.assertIsNotNone(part.latest_snapshot)
        self.assertEqual(part.latest_snapshot.status, "ok")
        self.assertEqual(part.latest_snapshot.mode, "user")
        self.assertEqual(part.latest_snapshot.frame_id, "donkey_frame_000000")
        self.assertIsNotNone(part.latest_snapshot.image)

    def test_local_mode_preserves_engine_pilot_outputs(self) -> None:
        manager = AutonomyManager()
        manager.engine = _PushyEngine()
        part = AutonomyPilotPart(
            host=AutonomyCycleHost(manager=manager),
            min_interval_s=0.0,
        )

        steering, throttle, control, _engine, _cycle = part.run(
            image_array=np.zeros((2, 2, 3), dtype=np.uint8),
            mode="local",
        )

        self.assertEqual(steering, 0.7)
        self.assertEqual(throttle, 0.4)
        self.assertEqual(control["steering"], 0.7)

    def test_bounded_cadence_uses_newest_frame_and_skips_intermediate_ticks(self) -> None:
        clock = _Clock(0.0)
        host = AutonomyCycleHost()
        part = AutonomyPilotPart(
            host=host,
            min_interval_s=0.5,
            monotonic=clock,
        )
        first = np.full((2, 2, 3), 1, dtype=np.uint8)
        second = np.full((2, 2, 3), 2, dtype=np.uint8)
        third = np.full((2, 2, 3), 3, dtype=np.uint8)

        part.run(image_array=first, mode="user")
        self.assertEqual(part.processed_count, 1)
        self.assertEqual(part.skipped_count, 0)
        self.assertEqual(int(part.latest_snapshot.image[0, 0, 0]), 1)

        clock.advance(0.2)
        part.run(image_array=second, mode="user")
        self.assertEqual(part.processed_count, 1)
        self.assertEqual(part.skipped_count, 1)
        self.assertEqual(int(part.latest_snapshot.image[0, 0, 0]), 1)

        clock.advance(0.4)
        part.run(image_array=third, mode="user")
        self.assertEqual(part.processed_count, 2)
        self.assertEqual(part.skipped_count, 1)
        self.assertEqual(int(part.latest_snapshot.image[0, 0, 0]), 3)
        self.assertEqual(part.latest_snapshot.skipped_since_previous, 1)
        self.assertEqual(part.latest_snapshot.frame_id, "donkey_frame_000001")
        self.assertEqual(host.manager.status()["step_count"], 2)

    def test_detaches_image_from_vehicle_memory(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost(), min_interval_s=0.0)
        image = np.zeros((3, 3, 3), dtype=np.uint8)
        part.run(image_array=image, mode="user")
        image[:] = 9
        self.assertEqual(int(part.latest_snapshot.image[0, 0, 0]), 0)

    def test_cycle_failure_keeps_zero_controls_and_records_error_snapshot(self) -> None:
        part = AutonomyPilotPart(host=_ExplodingHost(), min_interval_s=0.0)  # type: ignore[arg-type]

        steering, throttle, control, engine, cycle = part.run(
            image_array=np.zeros((2, 2, 3), dtype=np.uint8),
            mode="user",
        )

        self.assertEqual(steering, 0.0)
        self.assertEqual(throttle, 0.0)
        self.assertEqual(control["reason"], "observation-cycle-error")
        self.assertIsNone(cycle)
        self.assertEqual(engine, "exploding-host")
        self.assertEqual(part.latest_snapshot.status, "error")
        self.assertIn("RuntimeError", part.latest_snapshot.error or "")
        status = part.status()
        self.assertEqual(status["observation"]["processed_count"], 1)
        self.assertEqual(
            status["observation"]["latest"]["schema"],
            ONBOARD_OBSERVATION_SNAPSHOT_SCHEMA,
        )

    def test_status_omits_raw_image_payload(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost(), min_interval_s=0.0)
        part.run(image_array=np.ones((2, 2, 3), dtype=np.uint8), mode="user")
        latest = part.status()["observation"]["latest"]
        self.assertTrue(latest["has_image"])
        self.assertNotIn("image", latest)

    def test_manage_assembly_wires_always_on_observation(self) -> None:
        source = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "targets"
            / "donkeycar"
            / "app"
            / "manage.py"
        ).read_text(encoding="utf-8")
        marker = "autonomy_part = AutonomyPilotPart("
        self.assertIn(marker, source)
        snippet = source[source.index(marker) : source.index(marker) + 700]
        self.assertIn("min_interval_s=observation_interval_s", snippet)
        self.assertNotIn("run_condition", snippet)
        self.assertIn("AUTONOMY_OBSERVATION_INTERVAL_S", source)
        self.assertEqual(DEFAULT_OBSERVATION_INTERVAL_S, 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
