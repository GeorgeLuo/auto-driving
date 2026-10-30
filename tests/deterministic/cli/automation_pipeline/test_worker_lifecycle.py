from __future__ import annotations
from cli.automa_cli.hosts.runs.render import render_lifecycle_result
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.session import RunConfiguration
from implementations.runtime.picar import AutonomyPilotPart, create_host
from unittest.mock import patch
from cli.automa_cli.hosts.runs.models import RunResult
from cli.automa_cli.hosts.runs.process import wait_for_automation_startup
from cli.automa_cli.hosts.runs.store import record_host_automation_terminal_result
from cli.automa_cli.hosts.runs.service import (
    start_host_automation_background,
    stop_host_automation,
)
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    _ExitedProcess,
    _RunningProcess,
)


from tests.support.runtime_fixtures import write_host_fixture

class AutomationLivePipelineTests(unittest.TestCase):
    def test_picar_stale_decoder_cannot_stop_restart_or_overwrite_its_drive_mode(self):
        controller = SimpleNamespace(mode="user", angle=0., throttle=0., mode_latch=None)
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()), controller=controller)
        self.addCleanup(part.shutdown)
        decoded, fail_old, failed = threading.Event(), threading.Event(), threading.Event()
        starting, finish_start = threading.Event(), threading.Event()
        errors = []
        def old_loader():
            decoded.set()
            if not fail_old.wait(2):
                raise TimeoutError("test did not release old decoder")
            failed.set()
            raise ValueError("retired decoder")
        original_start = part.host.start
        def blocked_start(*args, **kwargs):
            starting.set()
            if not finish_start.wait(2):
                raise TimeoutError("test did not release start")
            return original_start(*args, **kwargs)
        def restart():
            try:
                part.start(RunConfiguration(mode="autonomy", num_decisions=None))
            except Exception as exc:
                errors.append(exc)
        part.start(RunConfiguration(mode="observe_only", num_decisions=None))
        part.submit(None, frame_id="old", image_loader=old_loader)
        self.assertTrue(decoded.wait(1))
        with patch.object(part.host, "start", side_effect=blocked_start):
            worker = threading.Thread(target=restart, daemon=True)
            worker.start()
            try:
                self.assertTrue(starting.wait(1))
                began = time.monotonic()
                part.publish_latest()
                self.assertLess(time.monotonic()-began, .25, "publication waited for lifecycle transport")
                fail_old.set()
                self.assertTrue(failed.wait(1))
            finally:
                fail_old.set()
                finish_start.set()
                worker.join(2)
                part.wait_for_cycle()
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(part.host.run_state, "running")
        self.assertEqual(part.host.execution.mode, "autonomy")
        self.assertEqual((controller.mode, controller.mode_latch), ("local", "local"))
        self.assertIsNone(part.host.last_error)


    def test_startup_accepts_a_correlated_view_that_advanced_during_export(self) -> None:
        state = {
            "status": "running", "pid": _RunningProcess.pid, "run_id": "current-run",
            "frames_received": 3, "processed_count": 2,
            "last_frame": {"frame_id": "frame_000001"},
        }
        view = {
            "available": True, "url": "http://127.0.0.1:8555/",
            "has_perception": True, "latest_perception_frame_id": "frame_000002",
            "perception_frame_buffered": True,
        }
        with (
            patch("cli.automa_cli.hosts.runs.process.read_run_state", return_value=state),
            patch("cli.automa_cli.hosts.runs.process.get_perception_view_status", return_value=view) as status,
        ):
            result = wait_for_automation_startup(
                process=_RunningProcess(), automation_dir=Path("unused"), timeout_s=0.2,
            )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["frame_id"], "frame_000002")
        self.assertEqual(status.call_args.kwargs["expected_run_id"], "current-run")
        self.assertEqual(status.call_args.kwargs["expected_worker_pid"], _RunningProcess.pid)

    def test_background_start_fails_when_child_exits_before_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            with (
                patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", runtime_root),
                patch(
                    "cli.automa_cli.hosts.runs.process.subprocess.Popen",
                    return_value=_ExitedProcess(),
                ),
            ):
                result = start_host_automation_background(
                    host_name="chase-sim-chaser",
                    take_control=False,
                    startup_wait_s=0.1,
                )

            self.assertEqual(result.exit_code, 2)
            self.assertIn("did not become ready", render_lifecycle_result(result))
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
            write_host_fixture(runtime_root, "chase-sim-chaser")
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
                            "frames_received": 1,
                            "processed_count": 1,
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
                                "perception_frame_buffered": True,
                            },
                        }
                    )
                    state_path.write_text(json.dumps(state), encoding="utf-8")

                threading.Thread(target=mark_ready, daemon=True).start()
                return _RunningProcess()

            with (
                patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.hosts.runs.process.subprocess.Popen", side_effect=launch),
                patch(
                    "cli.automa_cli.hosts.runs.process.get_perception_view_status",
                    return_value={
                        "status": "running",
                        "available": True,
                        "url": "http://127.0.0.1:8555/",
                        "has_frame": True,
                        "has_perception": True,
                        "latest_frame_id": "frame_000000",
                        "latest_perception_frame_id": "frame_000000",
                        "perception_frame_buffered": True,
                    },
                ),
                patch(
                    "cli.automa_cli.shared.open_view.webbrowser.open",
                    return_value=True,
                ) as browser_open,
            ):
                result = start_host_automation_background(
                    host_name="chase-sim-chaser",
                    take_control=False,
                    open_view=True,
                    startup_wait_s=1.0,
                )

            self.assertEqual(result.exit_code, 0, render_lifecycle_result(result))
            self.assertIn("Run started on host", render_lifecycle_result(result))
            self.assertNotIn("ready for:", render_lifecycle_result(result).lower())
            self.assertIn("frame_000000", render_lifecycle_result(result))
            self.assertIn("http://127.0.0.1:8555/", render_lifecycle_result(result))
            self.assertIn("Browser opened", render_lifecycle_result(result))
            browser_open.assert_called_once_with(
                "http://127.0.0.1:8555/",
                new=2,
            )

    def test_open_view_reuses_existing_worker_and_browser_failure_is_nonfatal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
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
                        "action_policy": "observe_only",
                        "control_application": "not_applied",
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
                "perception_frame_buffered": True,
            }

            with (
                patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.hosts.runs.service.pid_alive", return_value=True),
                patch(
                    "cli.automa_cli.hosts.runs.service.get_perception_view_status",
                    return_value=view,
                ),
                patch(
                    "cli.automa_cli.hosts.runs.process.subprocess.Popen",
                    side_effect=AssertionError("must not spawn a duplicate worker"),
                ),
                patch(
                    "cli.automa_cli.shared.open_view.webbrowser.open",
                    return_value=False,
                ) as browser_open,
            ):
                result = start_host_automation_background(
                    host_name="chase-sim-chaser",
                    take_control=False,
                    open_view=True,
                )

        self.assertEqual(result.exit_code, 0, render_lifecycle_result(result))
        self.assertIn("already has a run", render_lifecycle_result(result))
        self.assertNotIn("ready for:", render_lifecycle_result(result).lower())
        self.assertIn("Warning: could not open the browser", render_lifecycle_result(result))
        self.assertIn("Open manually: http://127.0.0.1:8666/", render_lifecycle_result(result))
        browser_open.assert_called_once_with("http://127.0.0.1:8666/", new=2)

    def test_existing_worker_must_match_requested_observation_only_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
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
                        "action_policy": "cycle_idle",
                        "control_application": "stop_only_safety_gate",
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.hosts.runs.service.pid_alive", return_value=True),
                patch(
                    "cli.automa_cli.hosts.runs.service.get_perception_view_status",
                    side_effect=AssertionError(
                        "mismatched authority is not inspectable"
                    ),
                ),
            ):
                result = start_host_automation_background(
                    host_name="chase-sim-chaser",
                    take_control=False,
                    open_view=True,
                )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("authority does not match", render_lifecycle_result(result))
        self.assertIn("action=observe_only, control=not_applied", render_lifecycle_result(result))
        self.assertNotIn("ready for:", render_lifecycle_result(result).lower())

    def test_stop_without_pid_records_stopped_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            automation_dir = (
                runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "automation"
            )
            automation_dir.mkdir(parents=True)
            (automation_dir / "state.json").write_text(
                json.dumps({"status": "error"}),
                encoding="utf-8",
            )

            with (
                patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", runtime_root),
            ):
                result = stop_host_automation(host_name="chase-sim-chaser")

            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("ready for:", render_lifecycle_result(result).lower())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["readiness"]["status"], "ready")
        self.assertEqual(
            state["readiness"]["gates"]["automation_worker"]["status"], "stopped"
        )

    def test_worker_early_failure_replaces_starting_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            automation_dir = (
                runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "automation"
            )
            automation_dir.mkdir(parents=True)
            (automation_dir / "state.json").write_text(
                json.dumps({"status": "starting", "pid": None}),
                encoding="utf-8",
            )
            with (
                patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", runtime_root),
            ):
                record_host_automation_terminal_result(
                    host_name="chase-sim-chaser",
                    result=RunResult(2, "No active Chase frontend was found."),
                )

            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "error")
            self.assertEqual(state["exit_code"], 2)
            self.assertIn("No active Chase frontend", state["error"])
