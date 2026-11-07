from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from autonomy.decision_cycle.action.publication import MODE_KEY, USER_STEERING_KEY, USER_THROTTLE_KEY
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.session import RunConfiguration
from tests.support.action_fixtures import fixed_control_steps
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.runtime.picar import AutonomyPilotPart, create_host


def _pushy_host(**steps) -> AutonomyCycleHost:
    """A host whose action step authorizes a fixed nonzero control."""

    return create_host(
        steps=fixed_control_steps(
            AutonomyControl(steering=0.7, throttle=0.4, confidence=1.0, reason="pushy-test-action"),
            **steps,
        )
    )


def _outputs(part: AutonomyPilotPart) -> tuple[float, float]:
    """The steering and throttle Donkey's next tick drives with."""

    output = part.host.execution.output()
    return output.steering, output.throttle


def _authorized(part: AutonomyPilotPart) -> dict:
    """The control the last published cycle authorized."""

    return part.latest_state.report["cycle"]["action"]["authority"]["authorized_output"]


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

        self.assertEqual(result.control.reason, "action_plan_invariant_violated")
        self.assertEqual(result.action.authority.gate_id, "selected")
        self.assertEqual(host.cycle_count, 1)
        self.assertTrue(
            result.to_dict()["context"]["sensor_frame"]["readings"][FRONT_CAMERA_SENSOR_ID][
                "has_value"
            ]
        )

    def test_donkey_part_returns_the_shared_cycle_shape(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))

        part.run(image_array=None, mode="local")
        part.wait_for_cycle()
        cycle = part.latest_state.report

        self.assertEqual(_outputs(part), (0.0, 0.0))
        self.assertEqual(_authorized(part)["reason"], "action_plan_invariant_violated")
        self.assertEqual(cycle["generation_id"], part.host.applied_decision()["generation_id"])
        self.assertEqual(cycle["schema"], "host_report")
        self.assertEqual(cycle["frame_id"], "donkey_frame_000000")

    def test_manual_mode_runs_cycle_and_forces_zero_pilot_outputs(self) -> None:
        part = AutonomyPilotPart(host=_pushy_host())

        part.run(
            image_array=np.zeros((4, 4, 3), dtype=np.uint8),
            mode="user",
            user_steering=0.2,
            user_throttle=0.1,
        )
        part.wait_for_cycle()
        cycle = part.latest_state.report

        self.assertEqual(_outputs(part), (0.0, 0.0))
        self.assertEqual(_authorized(part)["reason"], "pushy-test-action")
        self.assertEqual(_authorized(part)["steering"], 0.7)
        self.assertEqual(part.host.shared_memory[MODE_KEY], "manual")
        self.assertEqual(part.host.shared_memory[USER_STEERING_KEY], 0.2)
        self.assertEqual(part.host.shared_memory[USER_THROTTLE_KEY], 0.1)
        self.assertEqual(cycle["status"], "ok")
        self.assertEqual(cycle["application"]["mode"], "manual")
        self.assertEqual(part.latest_state.frame_id, "donkey_frame_000000")
        self.assertIsNotNone(part.latest_state.image)

    def test_local_mode_preserves_action_pilot_outputs(self) -> None:
        part = AutonomyPilotPart(host=_pushy_host())

        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="local")
        part.wait_for_cycle()

        self.assertEqual(_outputs(part), (0.7, 0.4))
        self.assertNotIn(USER_STEERING_KEY, part.host.shared_memory)
        self.assertNotIn(USER_THROTTLE_KEY, part.host.shared_memory)
        self.assertEqual(_authorized(part)["steering"], 0.7)

    def test_detaches_image_from_vehicle_memory(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        image = np.zeros((3, 3, 3), dtype=np.uint8)
        part.run(image_array=image, mode="user")
        image[:] = 9
        part.wait_for_cycle()
        self.assertEqual(int(part.latest_state.image[0, 0, 0]), 0)

    def test_a_raised_cycle_publishes_the_idle_write_it_made(self) -> None:
        def explode(context, perception):
            raise RuntimeError("observation failed")

        part = AutonomyPilotPart(host=_pushy_host(observation=explode))
        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="local")
        part.wait_for_cycle()

        # Execution zeroed the target for this frame; the report says so.
        application = part.publish_latest()["report"]["application"]
        self.assertEqual(
            (application["applied"], application["reason"], application["steering"], application["throttle"]),
            (True, "cycle-error", 0.0, 0.0),
        )

    def test_cycle_failure_keeps_zero_controls_and_records_error_state(self) -> None:
        def explode(*_args):
            raise RuntimeError("perception failed")

        part = AutonomyPilotPart(host=_pushy_host(perception=explode))

        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        cycle = part.latest_state.report

        self.assertEqual(_outputs(part), (0.0, 0.0))
        self.assertEqual(cycle["application"]["reason"], "observation-cycle-error")
        self.assertTrue(all(cycle["cycle"][step] is None for step in ("perception", "observation", "memory", "proposal", "plan", "action")))
        self.assertEqual(cycle["status"], "error")
        self.assertIn("RuntimeError", cycle["error"])
        status = part.observation_status()
        self.assertEqual(status["processed_count"], 1)
        self.assertEqual(status["latest"]["frame_id"], cycle["frame_id"])

    def test_memory_update_failure_stops_future_pilot_cycles(self) -> None:
        calls = 0

        def remember(_context):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("memory update failed")
            return None

        part = AutonomyPilotPart(host=_pushy_host(memory=remember))
        image = np.zeros((2, 2, 3), dtype=np.uint8)

        part.run(image_array=image, mode="local")
        part.wait_for_cycle()
        self.assertEqual(_outputs(part), (0.7, 0.4))

        part.run(image_array=image, mode="local")
        part.wait_for_cycle()
        self.assertEqual(_outputs(part), (0.0, 0.0))
        self.assertEqual(part.latest_state.report["status"], "error")

        # The halt keeps capturing but decides nothing more.
        part.run(image_array=image, mode="local")
        self.assertEqual(calls, 2)
        self.assertEqual(part.processed_count, 2)
        self.assertEqual(part.frames_received, 3)
        self.assertEqual(_outputs(part), (0.0, 0.0))

    def test_runtime_runs_set_the_controller_drive_mode(self) -> None:
        controller = SimpleNamespace(mode="user", mode_latch=None, angle=0.3, throttle=0.2)
        host = _pushy_host()
        part = AutonomyPilotPart(host=host, controller=controller)

        part.start(RunConfiguration(mode="autonomy", num_decisions=None))
        self.assertEqual((controller.mode, controller.mode_latch), ("local", "local"))
        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="local")
        self.assertEqual(host.run_state, "running")

        part.stop()
        self.assertEqual(
            (controller.mode, controller.mode_latch, controller.angle, controller.throttle),
            ("user", "user", 0.0, 0.0),
        )

        part.start(RunConfiguration(mode="observe_only", num_decisions=None))
        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        self.assertEqual(host.run_state, "running")
        self.assertEqual(controller.mode_latch, "user")

    def test_a_run_the_host_ends_hands_donkey_back_to_the_operator(self) -> None:
        controller = SimpleNamespace(mode="user", mode_latch=None, angle=0.3, throttle=0.2)
        host = _pushy_host()
        part = AutonomyPilotPart(host=host, controller=controller)
        image = np.zeros((2, 2, 3), dtype=np.uint8)

        part.start(RunConfiguration(mode="autonomy", num_decisions=1))
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
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        part.run(image_array=np.ones((2, 2, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        latest = part.observation_status()["latest"]
        self.assertEqual(
            set(latest), {"frame_id", "frame_index", "captured_at_ms", "completed_at_ms", "duration_ms"},
        )

    def test_observation_status_provider_does_not_reenter_host_status(self) -> None:
        host = create_host(steps=decision_steps())
        part = AutonomyPilotPart(host=host)
        part.run(image_array=np.zeros((2, 2, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()

        status = host.status()
        observation = status["components"]["observation"]
        self.assertEqual(observation["processed_count"], 1)
        self.assertEqual(observation["latest"]["frame_id"], "donkey_frame_000000")
        self.assertEqual(status["cycle_count"], 1)

    def test_manage_assembly_wires_always_on_observation(self) -> None:
        source = (
            Path(__file__).resolve().parents[4]
            / "deploy"
            / "targets"
            / "donkeycar"
            / "app"
            / "manage.py"
        ).read_text(encoding="utf-8")
        marker = "autonomy_part = AutonomyPilotPart("
        self.assertIn(marker, source)
        snippet = source[source.index(marker) : source.index(marker) + 1600]
        self.assertIn("source_id=source_id if telemetry_store is not None else None", snippet)
        self.assertIn("recording_root=host_runs_dir(runtime_root)", snippet)
        self.assertIn("staged_runtime(", source)
        self.assertIn("run_id=run_id if telemetry_store is not None else None", snippet)
        self.assertNotIn("run_condition", snippet)


if __name__ == "__main__":
    unittest.main(verbosity=2)
