"""Run lifecycle outcomes remain usable without terminal presentation."""
from __future__ import annotations

import io
import json
import signal
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cli.automa_cli.hosts.runs import process, service, store
from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.hosts.runs.models import RUN_EVENT_KINDS, RestartResult, RunEvent, StartResult, StopResult
from cli.automa_cli.hosts.runs.worker import RunOutput
from cli.automa_cli.hosts.runs.render import render_lifecycle_result


class LifecycleResultTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(service, "run_directory", return_value=self.root))
        self.stack.enter_context(patch.object(service, "staged_host", return_value={"provider": "chase-sim"}))
        self.stack.enter_context(patch.object(service, "bundle_paths", return_value={}))
        self.stack.enter_context(patch.object(service, "bundle_activation_problems", return_value=[]))
        self.stack.enter_context(patch.object(service, "running_base_url", return_value=None))

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def test_existing_start_verdicts_do_not_launch_or_print(self):
        self.write("process.json", {"pid": 42})
        base_state = {"status": "running", "pid": 42, "action_policy": "observe_only",
                      "control_application": "not_applied", "run_id": "current"}
        cases = [
            ({**base_state, "status": "starting"}, "starting", "still starting"),
            ({**base_state, "pid": 43}, "records_disagree", "records for host car disagree"),
            ({**base_state, "action_policy": "autonomy"}, "authority_mismatch", "authority does not match"),
            ({**base_state, "session": {"status": "stopped"}}, "error", "runtime session is stopped"),
            ({**base_state, "session": {"status": "error"}}, "error", "runtime session is error"),
            (base_state, "error", "runtime session is missing"),
        ]
        with patch.object(service, "pid_alive", return_value=True), \
             patch.object(service, "launch_worker") as launch:
            for state, kind, text in cases:
                self.write("state.json", state)
                stdout, stderr = io.StringIO(), io.StringIO()
                with self.subTest(kind=kind), redirect_stdout(stdout), redirect_stderr(stderr):
                    result = service.start_host_automation_background(host_name="car", take_control=False)
                self.assertEqual((result.exit_code, result.kind, result.pid), (2, kind, 42))
                self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))
                self.assertIn(text, render_lifecycle_result(result))
            launch.assert_not_called()

    def test_launch_failure_records_failure_and_returns_exception_facts(self):
        with patch.object(service, "launch_worker", side_effect=OSError("no process slots")):
            result = service.start_host_automation_background(host_name="car", take_control=False)
        self.assertEqual((result.exit_code, result.kind, result.error),
                         (2, "launch_failed", "OSError: no process slots"))
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual(state["status"], "error")
        self.assertIn("no process slots", state["error"])

    def test_stop_records_only_confirmed_worker_exit(self):
        for outcome, exit_code, stopped_by in [("stopped", 0, "stop_command"),
                                               ("forced", 0, "stop_command_forced"),
                                               ("timeout", 2, None)]:
            self.write("process.json", {"pid": 42})
            self.write("state.json", {"status": "running"})
            with self.subTest(outcome=outcome), patch.object(service, "pid_alive", return_value=True), \
                 patch.object(service, "pid_matches_automation", return_value=True), \
                 patch.object(service, "stop_worker", return_value=outcome) as stop:
                result = service.stop_host_automation(host_name="car", wait_s=0.25)
            stop.assert_called_once_with(42, wait_s=0.25)
            self.assertEqual((result.kind, result.exit_code), (outcome, exit_code))
            state = json.loads((self.root / "state.json").read_text())
            if stopped_by:
                self.assertEqual(state["status"], "stopped")
                record = json.loads((self.root / "process.json").read_text())
                self.assertEqual(record["stopped_by"], stopped_by)
            else:
                self.assertEqual(state["status"], "running")

    def test_stale_pid_is_reported_and_never_terminated(self):
        self.write("process.json", {"pid": 42})
        self.write("state.json", {"status": "running"})
        with patch.object(service, "pid_alive", return_value=True), \
             patch.object(service, "pid_matches_automation", return_value=False), \
             patch.object(service, "process_command", return_value="unrelated process"), \
             patch.object(service, "stop_worker") as stop:
            result = service.stop_host_automation(host_name="car")
        stop.assert_not_called()
        self.assertEqual((result.exit_code, result.kind, result.command), (0, "stale_pid", "unrelated process"))
        self.assertIn("no process was terminated", render_lifecycle_result(result))

    def test_restart_does_not_start_after_failed_stop(self):
        failure = StopResult(2, "remote_failed", "car", error="offline")
        with patch.object(service, "stop_host_automation", return_value=failure), \
             patch.object(service, "start_host_automation_background") as start:
            result = service.restart_host_automation(host_name="car", take_control=False)
        self.assertEqual(result, RestartResult(2, "stop_failed", "car", stop=failure))
        self.assertEqual(render_lifecycle_result(result), "Could not stop car: offline")
        start.assert_not_called()

    def test_restart_returns_both_outcomes_in_order(self):
        stopped = StopResult(0, "no_pid", "car", state_path=self.root / "state.json")
        started = StartResult(2, "launch_failed", "car", error="no process slots")
        with patch.object(service, "stop_host_automation", return_value=stopped), \
             patch.object(service, "start_host_automation_background", return_value=started):
            result = service.restart_host_automation(host_name="car", take_control=False)
        self.assertEqual(result, RestartResult(2, "start_failed", "car", stop=stopped, start=started))
        self.assertEqual(render_lifecycle_result(result),
                         render_lifecycle_result(stopped) + "\n\n" + render_lifecycle_result(started))


class WorkerProcessTests(unittest.TestCase):
    def test_stop_escalates_only_when_graceful_wait_expires(self):
        for waits, outcome, signals in [([True], "stopped", [signal.SIGINT]),
                                        ([False, True], "forced", [signal.SIGINT, signal.SIGKILL]),
                                        ([False, False], "timeout", [signal.SIGINT, signal.SIGKILL])]:
            with self.subTest(outcome=outcome), patch.object(process, "_wait_for_exit", side_effect=waits), \
                 patch.object(process, "terminate_pid") as terminate:
                self.assertEqual(process.stop_worker(42, wait_s=0.25), outcome)
            self.assertEqual([call.args[1] for call in terminate.call_args_list], signals)
            self.assertEqual(terminate.call_args_list[0].kwargs, {"process_group": False})

    def test_startup_timeout_names_the_session_without_killing_worker(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(process.time, "monotonic", side_effect=[0.0, 2.0]), \
             patch.object(process, "terminate_pid") as terminate:
            result = process.wait_for_automation_startup(
                process=SimpleNamespace(pid=43434, poll=lambda: None),
                automation_dir=Path(directory),
                timeout_s=1.0,
            )
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("phases", result)
        self.assertIn("runtime session is missing", result["reason"])
        self.assertIn("worker is alive", result["reason"])
        terminate.assert_not_called()

    def test_running_session_with_no_decisions_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text(json.dumps({
                "pid": 43434,
                "run_id": "run-1",
                "session": {"status": "running", "processed_decisions": 0},
            }))
            result = process.wait_for_automation_startup(
                process=SimpleNamespace(pid=43434, poll=lambda: None),
                automation_dir=root,
                timeout_s=0.2,
            )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["processed_count"], 0)
        self.assertNotIn("view_url", result)

    def test_exited_worker_with_a_completed_session_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text(json.dumps({
                "pid": 42424,
                "run_id": "run-1",
                "session": {"status": "completed", "processed_decisions": 2},
            }))
            result = process.wait_for_automation_startup(
                process=SimpleNamespace(pid=42424, poll=lambda: 7),
                automation_dir=root,
                timeout_s=0.2,
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["processed_count"], 2)

    def test_exited_worker_whose_session_still_says_running_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text(json.dumps({
                "pid": 42424,
                "run_id": "run-1",
                "session": {"status": "running", "processed_decisions": 1},
            }))
            result = process.wait_for_automation_startup(
                process=SimpleNamespace(pid=42424, poll=lambda: 7),
                automation_dir=root,
                timeout_s=0.2,
            )
        self.assertEqual(result["status"], "failed")
        self.assertIn("exited with code 7", result["reason"])
        self.assertIn("before the runtime session completed", result["reason"])

    def test_completed_session_does_not_hide_a_failed_recording_export(self):
        for exit_code in (None, 2):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = {
                    "pid": 42424,
                    "run_id": "run-1",
                    "session": {"status": "completed", "processed_decisions": 2},
                    "recording": True,
                    "recorded_count": 1,
                    "processed_count": 2,
                }
                store.finish_run(state, status="completed")
                (root / "state.json").write_text(json.dumps(state))
                result = process.wait_for_automation_startup(
                    process=SimpleNamespace(pid=42424, poll=lambda: exit_code),
                    automation_dir=root,
                    timeout_s=0.2,
                )
                self.assertEqual(result["status"], "failed")
                self.assertIn("Recording count does not match", result["reason"])
                self.assertEqual(result["state"]["session"]["status"], "completed")
                self.assertEqual(result["state"]["readiness"]["status"], "ready")

    def test_exited_worker_with_a_changed_run_id_is_not_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text(json.dumps({
                "pid": 42424,
                "run_id": "run-1",
                "session": {"status": "starting"},
            }))
            calls = {"n": 0}

            def poll():
                calls["n"] += 1
                if calls["n"] == 1:
                    return None
                (root / "state.json").write_text(json.dumps({
                    "pid": 42424,
                    "run_id": "run-2",
                    "session": {"status": "completed", "processed_decisions": 1},
                }))
                return 7

            result = process.wait_for_automation_startup(
                process=SimpleNamespace(pid=42424, poll=poll),
                automation_dir=root,
                timeout_s=0.2,
            )
        self.assertEqual(result["status"], "failed")
        self.assertIn("run id changed from run-1 to run-2", result["reason"])


class ExplicitViewTests(unittest.TestCase):
    """A run serves no page; its output names the command that does."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(service, "run_directory", return_value=self.root))
        self.stack.enter_context(patch.object(service, "staged_host", return_value={"provider": "chase-sim"}))
        self.stack.enter_context(patch.object(service, "bundle_paths", return_value={}))
        self.stack.enter_context(patch.object(service, "bundle_activation_problems", return_value=[]))
        self.stack.enter_context(patch.object(service, "running_base_url", return_value=None))
        no_view = AssertionError("a run start made a viewer request")
        self.stack.enter_context(patch("cli.automa_cli.shared.open_view.webbrowser.open", side_effect=no_view))
        self.stack.enter_context(patch.object(RuntimeViewServer, "__init__", side_effect=no_view))

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def test_a_started_or_completed_run_names_the_viewer_command(self):
        for status in ("ready", "completed"):
            startup = {"status": status, "session_status": "running", "processed_count": 0, "state": {}}
            with self.subTest(status=status), \
                 patch.object(service, "launch_worker", return_value=SimpleNamespace(pid=43434)), \
                 patch.object(service, "wait_for_automation_startup", return_value=startup):
                result = service.start_host_automation_background(host_name="car", take_control=False)
            self.assertEqual(result.exit_code, 0)
            rendered = render_lifecycle_result(result)
            if status == "ready":
                self.assertIn("View: ./cli/automa hosts viewer --name car", rendered)
            self.assertNotIn("Browser", rendered)

    def test_reusing_a_live_worker_names_the_viewer_command(self):
        self.write("process.json", {"pid": 42})
        self.write("state.json", {
            "status": "running", "pid": 42, "run_id": "current", "action_policy": "observe_only",
            "control_application": "not_applied", "session": {"status": "running", "processed_decisions": 0},
        })
        with patch.object(service, "pid_alive", return_value=True), \
             patch.object(service, "launch_worker") as launch:
            result = service.start_host_automation_background(host_name="car", take_control=False)
        launch.assert_not_called()
        self.assertEqual((result.exit_code, result.kind), (0, "existing"))
        self.assertIn("View: ./cli/automa hosts viewer --name car", render_lifecycle_result(result))


class RunEventTests(unittest.TestCase):
    SAMPLES = {
        "startup": {
            "host_name": "car", "selections": {"perception": {"plugins": ["frame"], "preset": None}},
            "run_dir": None, "control_source": "runtime_host", "action_policy": "observe_only",
            "decision": {"generation_id": "g-1"}, "num_decisions": None,
        },
    }

    def test_every_run_event_kind_is_written(self):
        self.assertEqual(set(self.SAMPLES), set(RUN_EVENT_KINDS))
        for kind, data in self.SAMPLES.items():
            with self.subTest(kind=kind):
                output = io.StringIO()
                RunOutput(output)(RunEvent(kind, data))
                self.assertTrue(output.getvalue().strip())

    def test_an_unknown_run_event_cannot_be_emitted(self):
        with self.assertRaisesRegex(ValueError, "Unknown run event"):
            RunEvent("paused", {})
