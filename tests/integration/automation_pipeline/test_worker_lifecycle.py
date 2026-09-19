from __future__ import annotations
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from cli.automa_cli.automation import (
    CommandResult,
    record_vehicle_automation_terminal_result,
    start_vehicle_automation_background,
    stop_vehicle_automation,
)
from tests.integration.automation_pipeline.pipeline_fixtures import (
    _ExitedProcess,
    _RunningProcess,
)


class AutomationLivePipelineTests(unittest.TestCase):
    def test_background_start_fails_when_child_exits_before_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch(
                    "cli.automa_cli.automation.subprocess.Popen",
                    return_value=_ExitedProcess(),
                ),
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
                runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "automation"
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
                            "frames_processed": 1,
                            "last_capture": {
                                "frame_id": "frame_000000",
                                "capture_duration_ms": 4,
                            },
                            "last_frame": {
                                "frame_id": "frame_000000",
                                "perception_duration_ms": 8,
                            },
                            "published_view": {
                                "status": "running",
                                "available": True,
                                "url": "http://127.0.0.1:8555/",
                                "has_frame": True,
                                "has_perception": True,
                                "latest_frame_id": "frame_000000",
                                "latest_perception_frame_id": "frame_000000",
                            },
                        }
                    )
                    state_path.write_text(json.dumps(state), encoding="utf-8")

                threading.Thread(target=mark_ready, daemon=True).start()
                return _RunningProcess()

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation.subprocess.Popen", side_effect=launch),
                patch(
                    "cli.automa_cli.automation.get_perception_view_status",
                    return_value={
                        "status": "running",
                        "available": True,
                        "url": "http://127.0.0.1:8555/",
                        "has_frame": True,
                        "has_perception": True,
                        "latest_frame_id": "frame_000000",
                        "latest_perception_frame_id": "frame_000000",
                    },
                ),
                patch(
                    "cli.automa_cli.automation.webbrowser.open",
                    return_value=True,
                ) as browser_open,
            ):
                result = start_vehicle_automation_background(
                    vehicle_id="chase-sim-chaser",
                    open_view=True,
                    startup_wait_s=1.0,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertIn("Automation ready", result.message)
            self.assertIn(
                "Ready for: inspect perception and stop automation",
                result.message,
            )
            self.assertIn("frame_000000", result.message)
            self.assertIn("http://127.0.0.1:8555/", result.message)
            self.assertIn("Browser opened", result.message)
            browser_open.assert_called_once_with(
                "http://127.0.0.1:8555/",
                new=2,
            )

    def test_open_view_reuses_existing_worker_and_browser_failure_is_nonfatal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            automation_dir = (
                runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "automation"
            )
            automation_dir.mkdir(parents=True)
            (automation_dir / "process.json").write_text(
                json.dumps({"pid": 45454, "log_to_disk": False}),
                encoding="utf-8",
            )
            (automation_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "pid": 45454,
                        "run_id": "run-current",
                        "action_policy": "engine_idle",
                        "control_application": "stop_only_safety_gate",
                    }
                ),
                encoding="utf-8",
            )
            view = {
                "status": "running",
                "available": True,
                "url": "http://127.0.0.1:8666/",
                "has_frame": True,
                "has_perception": True,
                "latest_frame_id": "frame_000001",
                "latest_perception_frame_id": "frame_000001",
            }

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation._pid_alive", return_value=True),
                patch(
                    "cli.automa_cli.automation.get_perception_view_status",
                    return_value=view,
                ),
                patch(
                    "cli.automa_cli.automation.subprocess.Popen",
                    side_effect=AssertionError("must not spawn a duplicate worker"),
                ),
                patch(
                    "cli.automa_cli.automation.webbrowser.open",
                    return_value=False,
                ) as browser_open,
            ):
                result = start_vehicle_automation_background(
                    vehicle_id="chase-sim-chaser",
                    open_view=True,
                )

        self.assertEqual(result.exit_code, 0, result.message)
        self.assertIn("Automation already running", result.message)
        self.assertIn(
            "Ready for: inspect perception and stop automation",
            result.message,
        )
        self.assertIn("Warning: could not open the browser", result.message)
        self.assertIn("Open manually: http://127.0.0.1:8666/", result.message)
        browser_open.assert_called_once_with("http://127.0.0.1:8666/", new=2)

    def test_existing_worker_must_match_requested_observation_only_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            automation_dir = (
                runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "automation"
            )
            automation_dir.mkdir(parents=True)
            (automation_dir / "process.json").write_text(
                json.dumps({"pid": 45454}),
                encoding="utf-8",
            )
            (automation_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "pid": 45454,
                        "run_id": "run-control-taking",
                        "action_policy": "engine_idle",
                        "control_application": "stop_only_safety_gate",
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation._pid_alive", return_value=True),
                patch(
                    "cli.automa_cli.automation.get_perception_view_status",
                    side_effect=AssertionError(
                        "mismatched authority is not inspectable"
                    ),
                ),
            ):
                result = start_vehicle_automation_background(
                    vehicle_id="chase-sim-chaser",
                    take_control=False,
                    open_view=True,
                )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("authority does not match", result.message)
        self.assertIn("action=observe_only, control=not_applied", result.message)
        self.assertIn("Not ready for: inspect perception", result.message)

    def test_stop_without_pid_records_stopped_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            automation_dir = (
                runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "automation"
            )
            automation_dir.mkdir(parents=True)
            (automation_dir / "state.json").write_text(
                json.dumps({"status": "error"}),
                encoding="utf-8",
            )

            with patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root):
                result = stop_vehicle_automation(vehicle_id="chase-sim-chaser")

            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Ready for: inspect stopped deployment", result.message)
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["readiness"]["status"], "ready")
        self.assertEqual(
            state["readiness"]["gates"]["automation_worker"]["status"], "stopped"
        )

    def test_foreground_early_failure_replaces_starting_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            automation_dir = (
                runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "automation"
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

            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "error")
            self.assertEqual(state["exit_code"], 2)
            self.assertIn("No active Chase frontend", state["error"])
