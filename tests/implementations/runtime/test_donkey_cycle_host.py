from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.execution import ControlExecution
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.session import RunConfiguration
from tests.support.action_fixtures import fixed_control_steps
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.runtime.picar import (
    DEFAULT_INTERVAL_S,
    AutonomyPilotPart,
    DonkeyControlTarget,
    ONBOARD_OBSERVATION_STATE_SCHEMA,
    create_host,
)


def _pushy_host(**steps) -> AutonomyCycleHost:
    """A host whose action step authorizes a fixed nonzero control."""

    return create_host(
        steps=fixed_control_steps(
            AutonomyControl(steering=0.7, throttle=0.4, confidence=1.0, reason="pushy-test-action"),
            **steps,
        )
    )


class _ExplodingHost:
    execution = ControlExecution(DonkeyControlTarget())
    last_context = None

    def status(self) -> dict:
        return {"steps": {}, "last_cycle": None}

    def run(self, context: DecisionFrameContext):
        del context
        raise RuntimeError("cycle failed")


class RuntimeCycleHostTests(unittest.TestCase):
    def test_host_runs_the_cycle_with_in_memory_front_camera_value(self) -> None:
        image_value = object()
        sensor_frame = SensorFrame(
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
                sensor_frame=sensor_frame,
            )
        )

        self.assertEqual(result.control.reason, "no-selected-command")
        self.assertEqual(result.action.authority.gate_id, "selected")
        self.assertEqual(host.cycle_count, 1)
        self.assertTrue(
            result.to_dict()["context"]["sensor_frame"]["readings"][FRONT_CAMERA_SENSOR_ID][
                "has_value"
            ]
        )

    def test_donkey_part_returns_the_shared_cycle_shape(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()), interval_s=0.0)

        part.run(image_array=object(), mode="local")
        part.wait_for_cycle()
        steering, throttle, control, generation, cycle = part.completed_outputs("local")

        self.assertEqual(steering, 0.0)
        self.assertEqual(throttle, 0.0)
        self.assertEqual(control["reason"], "no-selected-command")
        self.assertIsNone(generation)
        self.assertEqual(cycle["schema"], "decision_cycle_result_v1")
        self.assertEqual(cycle["context"]["frame_id"], "donkey_frame_000000")

    def test_manual_mode_runs_cycle_and_forces_zero_pilot_outputs(self) -> None:
        part = AutonomyPilotPart(host=_pushy_host(), interval_s=0.0)

        part.run(
            image_array=np.zeros((4, 4, 3), dtype=np.uint8),
            mode="user",
            user_steering=0.2,
            user_throttle=0.1,
        )
        part.wait_for_cycle()
        steering, throttle, control, _generation, cycle = part.completed_outputs("user")

        self.assertEqual(steering, 0.0)
        self.assertEqual(throttle, 0.0)
        self.assertEqual(control["reason"], "pushy-test-action")
        self.assertEqual(control["steering"], 0.7)
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle["context"]["mode"], "manual")
        self.assertIsNotNone(part.latest_state)
        self.assertEqual(part.latest_state.status, "ok")
        self.assertEqual(part.latest_state.mode, "manual")
        self.assertEqual(part.latest_state.frame_id, "donkey_frame_000000")
        self.assertIsNotNone(part.latest_state.image)

    def test_local_mode_preserves_action_pilot_outputs(self) -> None:
        part = AutonomyPilotPart(host=_pushy_host(), interval_s=0.0)

        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="local")
        part.wait_for_cycle()
        steering, throttle, control, _generation, _cycle = part.completed_outputs("local")

        self.assertEqual(steering, 0.7)
        self.assertEqual(throttle, 0.4)
        self.assertEqual(control["steering"], 0.7)

    def test_detaches_image_from_vehicle_memory(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()), interval_s=0.0)
        image = np.zeros((3, 3, 3), dtype=np.uint8)
        part.run(image_array=image, mode="user")
        image[:] = 9
        self.assertEqual(int(part.latest_camera_frame.image[0, 0, 0]), 0)
        part.wait_for_cycle()
        self.assertEqual(int(part.latest_state.image[0, 0, 0]), 0)

    def test_cycle_failure_keeps_zero_controls_and_records_error_state(self) -> None:
        part = AutonomyPilotPart(host=_ExplodingHost(), interval_s=0.0)  # type: ignore[arg-type]

        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        steering, throttle, control, generation, cycle = part.completed_outputs("user")

        self.assertEqual(steering, 0.0)
        self.assertEqual(throttle, 0.0)
        self.assertEqual(control["reason"], "observation-cycle-error")
        self.assertIsNone(cycle)
        self.assertIsNone(generation)
        self.assertEqual(part.latest_state.status, "error")
        self.assertIn("RuntimeError", part.latest_state.error or "")
        status = part.observation_status()
        self.assertEqual(status["processed_count"], 1)
        self.assertEqual(
            status["latest"]["schema"],
            ONBOARD_OBSERVATION_STATE_SCHEMA,
        )

    def test_memory_update_failure_stops_future_pilot_cycles(self) -> None:
        calls = 0

        def remember(_context, _observation):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("memory update failed")
            return None

        part = AutonomyPilotPart(host=_pushy_host(memory=remember), interval_s=0.0)
        image = np.zeros((2, 2, 3), dtype=np.uint8)

        part.run(image_array=image, mode="local")
        part.wait_for_cycle()
        self.assertEqual(part.completed_outputs("local")[:2], (0.7, 0.4))

        part.run(image_array=image, mode="local")
        part.wait_for_cycle()
        self.assertEqual(part.completed_outputs("local")[:2], (0.0, 0.0))
        self.assertEqual(part.latest_state.status, "error")
        self.assertTrue(part.observation_status()["memory_update_halted"])

        part.run(image_array=image, mode="local")
        self.assertEqual(calls, 2)
        self.assertEqual(part.processed_count, 2)
        self.assertEqual(part.completed_outputs("local")[:2], (0.0, 0.0))

    def test_runtime_runs_set_the_controller_drive_mode(self) -> None:
        controller = SimpleNamespace(mode="user", mode_latch=None, angle=0.3, throttle=0.2)
        host = _pushy_host()
        part = AutonomyPilotPart(host=host, interval_s=0.0, controller=controller)

        part.start(RunConfiguration(mode="autonomy", interval_s=0.0))
        self.assertEqual((controller.mode, controller.mode_latch), ("local", "local"))
        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="local")
        self.assertEqual(host.run_state, "running")

        part.stop()
        self.assertEqual(
            (controller.mode, controller.mode_latch, controller.angle, controller.throttle),
            ("user", "user", 0.0, 0.0),
        )

        part.start(RunConfiguration(mode="observe_only", interval_s=0.0))
        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        self.assertEqual(host.run_state, "running")
        self.assertEqual(controller.mode_latch, "user")

    def test_a_run_the_host_ends_hands_donkey_back_to_the_operator(self) -> None:
        controller = SimpleNamespace(mode="user", mode_latch=None, angle=0.3, throttle=0.2)
        host = _pushy_host()
        part = AutonomyPilotPart(host=host, interval_s=0.0, controller=controller)
        image = np.zeros((2, 2, 3), dtype=np.uint8)

        part.start(RunConfiguration(mode="autonomy", interval_s=0.0, num_decisions=1))
        part.run(image_array=image, mode="local")
        part.wait_for_cycle()
        self.assertEqual(host.run_state, "completed")

        # Donkey still reports local until the controller applies the latch.
        part.run(image_array=image, mode="local")
        part.run(image_array=image, mode="local")
        self.assertEqual(
            (controller.mode_latch, controller.angle, controller.throttle),
            ("user", 0.0, 0.0),
        )
        self.assertEqual(host.run_state, "completed")

        part.run(image_array=image, mode="user")
        part.run(image_array=image, mode="local")
        self.assertEqual(host.run_state, "running")

    def test_status_omits_raw_image_payload(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()), interval_s=0.0)
        part.run(image_array=np.ones((2, 2, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        latest = part.observation_status()["latest"]
        self.assertTrue(latest["has_image"])
        self.assertNotIn("image", latest)

    def test_observation_status_provider_does_not_reenter_host_status(self) -> None:
        host = create_host(steps=decision_steps())
        part = AutonomyPilotPart(host=host, interval_s=0.0)
        host.register_status_provider("observation", part.observation_status)
        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()

        status = host.status()
        observation = status["components"]["observation"]
        self.assertEqual(observation["processed_count"], 1)
        self.assertEqual(observation["latest"]["frame_id"], "donkey_frame_000000")
        self.assertEqual(status["cycle_count"], 1)

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
        snippet = source[source.index(marker) : source.index(marker) + 1600]
        self.assertIn("interval_s=capture_interval_s", snippet)
        self.assertIn("source_id=source_id if telemetry_store is not None else None", snippet)
        self.assertIn("decision_activations=decision_activations", snippet)
        self.assertIn("generation_id=generation_id", snippet)
        self.assertIn("run_id=run_id if telemetry_store is not None else None", snippet)
        self.assertIn("autonomy_controller.observation_publisher = autonomy_part", snippet)
        self.assertIn("autonomy_controller.autonomy_host = host", snippet)
        self.assertNotIn("run_condition", snippet)
        self.assertIn("AUTONOMY_CAPTURE_INTERVAL_S", source)
        self.assertEqual(DEFAULT_INTERVAL_S, 0.25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
