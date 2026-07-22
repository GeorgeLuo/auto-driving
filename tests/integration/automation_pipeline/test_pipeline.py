from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.perception import PERCEPTION_TEXT_SCHEMA, PerceptionText
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from cli.automa_cli.automation import (
    CommandResult,
    record_vehicle_automation_terminal_result,
    run_vehicle_automation,
    start_vehicle_automation_background,
)
from cli.automa_cli.bundles import controller_bundle_paths


class _SlowMapper:
    def __init__(self) -> None:
        self.frame_ids: list[str] = []

    def perceive(self, request):
        self.frame_ids.append(request.snapshot.read_id)
        time.sleep(0.05)
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test.slow-mapper",
            status="empty",
            lines=(f"schema={PERCEPTION_TEXT_SCHEMA}", "plugin=test.slow-mapper"),
            signals=(),
            things=(),
        )


class _FakeCar:
    def __init__(self, **_kwargs) -> None:
        self.capture_count = 0
        self.last_capture_shadow_reference: dict | None = None
        self.last_simulator_frame_index: int | None = None

    def read_sensors(self, request):
        now_ms = int(time.time() * 1000)
        path = request.front_camera_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Simulate advancing Chase play_debug frameIndex values.
        simulator_frame_index = 100 + self.capture_count
        Image.new("RGB", (64, 48), (self.capture_count % 256, 40, 60)).save(path)
        self.capture_count += 1
        self.last_simulator_frame_index = simulator_frame_index
        self.last_capture_shadow_reference = {
            "schema": "chase_shadow_reference_v1",
            "evaluator_only": True,
            "simulator_frame_index": simulator_frame_index,
            "simulation_epoch": "chase-run:test",
            "frame_id": f"chase_frame_{simulator_frame_index:06d}",
            "game_id": "chase",
            "scenario": "chaser-depth-obstacles",
            "chaser_control_source": "builtin",
        }
        reading = SensorReading(
            sensor_id=FRONT_CAMERA_SENSOR_ID,
            sensor_kind="camera",
            captured_at_ms=now_ms,
            path=str(path),
            metadata={
                "content_type": "image/png",
                "simulator_frame_index": simulator_frame_index,
                "simulation_epoch": "chase-run:test",
                "frame_index": simulator_frame_index,
                "frame_id": f"chase_frame_{simulator_frame_index:06d}",
            },
        )
        return SensorSnapshot(
            read_id=request.read_id,
            readings={FRONT_CAMERA_SENSOR_ID: reading},
            started_at_ms=now_ms,
            completed_at_ms=now_ms,
            request=request.to_dict(),
            metadata={
                "simulator_frame_index": simulator_frame_index,
                "simulation_epoch": "chase-run:test",
                "frame_id": f"chase_frame_{simulator_frame_index:06d}",
            },
        )


class _ExitedProcess:
    pid = 42424

    def poll(self):
        return 7


class _RunningProcess:
    pid = 43434

    def poll(self):
        return None


class AutomationLivePipelineTests(unittest.TestCase):
    def test_capture_does_not_wait_for_slow_perception_and_latest_frame_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            bundle = controller_bundle_paths(runtime_root / vehicle_id)
            _write_activations(bundle)
            mapper = _SlowMapper()
            vehicle = {
                "id": vehicle_id,
                "provider": "chase-sim",
                "connection": {"ws_url": "ws://unused"},
            }

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation.discover_active_vehicles", return_value={}),
                patch("cli.automa_cli.automation.find_vehicle_by_id", return_value=(vehicle, None)),
                patch("cli.automa_cli.automation.ChaseSimCar", _FakeCar),
                patch("cli.automa_cli.automation._load_mapper", return_value=mapper),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id,
                    interval_s=0.005,
                    frames=8,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads((automation_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["frames_captured"], 8)
            self.assertLess(state["frames_processed"], state["frames_captured"])
            self.assertEqual(
                state["frames_processed"] + state["frames_dropped"],
                state["frames_captured"],
            )
            self.assertEqual(mapper.frame_ids[-1], "chase_frame_000107")
            self.assertTrue(
                (automation_dir / "latest" / "frames" / "latest_front_camera.png").is_file()
            )
            latest = json.loads(
                (automation_dir / "latest_perception.json").read_text(encoding="utf-8")
            )
            self.assertEqual(latest["simulator_frame_index"], 107)
            self.assertEqual(latest["simulation_epoch"], "chase-run:test")
            self.assertEqual(latest["frame_id"], "chase_frame_000107")
            self.assertEqual(
                latest["shadow_reference"]["simulator_frame_index"],
                latest["simulator_frame_index"],
            )
            self.assertIs(latest["control"]["applied"], False)
            self.assertNotIn("shadow_reference", latest.get("observation") or {})
            self.assertEqual(
                list((automation_dir / "latest" / "frames").glob("frame_*_front_camera.png")),
                [],
            )

    def test_background_start_fails_when_child_exits_before_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation.subprocess.Popen", return_value=_ExitedProcess()),
            ):
                result = start_vehicle_automation_background(
                    vehicle_id="chase-sim-chaser",
                    startup_wait_s=0.1,
                )

            self.assertEqual(result.exit_code, 2)
            self.assertIn("did not become ready", result.message)
            state_path = (
                runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "automation"
                / "state.json"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "error")
            self.assertIn("exited with code 7", state["error"])

    def test_background_start_returns_only_after_first_frame_and_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            automation_dir = (
                runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "automation"
            )

            def launch(*_args, **_kwargs):
                def mark_ready() -> None:
                    time.sleep(0.02)
                    state_path = automation_dir / "state.json"
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    state.update(
                        {
                            "status": "running",
                            "pid": _RunningProcess.pid,
                            "frames_captured": 1,
                            "last_capture": {"frame_id": "frame_000000"},
                            "published_view": {
                                "status": "running",
                                "available": True,
                                "url": "http://127.0.0.1:8555/",
                            },
                        }
                    )
                    state_path.write_text(json.dumps(state), encoding="utf-8")

                threading.Thread(target=mark_ready, daemon=True).start()
                return _RunningProcess()

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation.subprocess.Popen", side_effect=launch),
            ):
                result = start_vehicle_automation_background(
                    vehicle_id="chase-sim-chaser",
                    startup_wait_s=1.0,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertIn("Automation ready", result.message)
            self.assertIn("frame_000000", result.message)
            self.assertIn("http://127.0.0.1:8555/", result.message)

    def test_foreground_early_failure_replaces_starting_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            automation_dir = (
                runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "automation"
            )
            automation_dir.mkdir(parents=True)
            (automation_dir / "state.json").write_text(
                json.dumps({"status": "starting", "pid": None}),
                encoding="utf-8",
            )
            with patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root):
                record_vehicle_automation_terminal_result(
                    vehicle_id="chase-sim-chaser",
                    result=CommandResult(2, "No active Chase frontend was found."),
                )

            state = json.loads((automation_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "error")
            self.assertEqual(state["exit_code"], 2)
            self.assertIn("No active Chase frontend", state["error"])


def _write_activations(bundle: dict[str, str]) -> None:
    perception_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    decision_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    perception_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    perception_path.write_text(
        json.dumps(
            {
                "schema": "automa_perception_activation_v0",
                "controller_bundle": {"root_dir": bundle["root_dir"]},
                "perception": {
                    "algorithm": "test",
                    "mapper_spec": "test:SlowMapper",
                    "mapper_config": {},
                },
            }
        ),
        encoding="utf-8",
    )
    decision_path.write_text(
        json.dumps(
            {
                "schema": "automa_decision_activation_v0",
                "controller_bundle": {"root_dir": bundle["root_dir"]},
                "decision": {
                    "engine_id": "idle",
                    "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
                    "engine_config": {},
                },
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
