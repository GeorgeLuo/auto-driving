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

from cli.automa_cli.hosts.runs import process, service
from cli.automa_cli.hosts.runs.models import RestartResult, StartResult, StopResult
from cli.automa_cli.hosts.runs.render import render_lifecycle_result


class LifecycleResultTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(service, "host_automation_dir", return_value=self.root))
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
            (base_state, "view_unready", "perception view is not ready"),
        ]
        with patch.object(service, "pid_alive", return_value=True), \
             patch.object(service, "get_perception_view_status", return_value={"reason": "waiting"}), \
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
        self.assertEqual(result, RestartResult(2, kind="stop_failed", stop=failure))
        self.assertEqual(render_lifecycle_result(result), "Could not stop car: offline")
        start.assert_not_called()

    def test_restart_returns_both_outcomes_in_order(self):
        stopped = StopResult(0, "no_pid", "car", state_path=self.root / "state.json")
        started = StartResult(2, "launch_failed", "car", error="no process slots")
        with patch.object(service, "stop_host_automation", return_value=stopped), \
             patch.object(service, "start_host_automation_background", return_value=started):
            result = service.restart_host_automation(host_name="car", take_control=False)
        self.assertEqual(result, RestartResult(2, stop=stopped, start=started))
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

    def test_startup_timeout_reports_incomplete_phases_without_killing_worker(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(process.time, "monotonic", side_effect=[0.0, 2.0]), \
             patch.object(process, "terminate_pid") as terminate:
            result = process.wait_for_automation_startup(
                process=SimpleNamespace(poll=lambda: None), automation_dir=Path(directory), timeout_s=1.0,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual({phase["status"] for phase in result["phases"].values()}, {"incomplete"})
        self.assertIn("still running", result["reason"])
        terminate.assert_not_called()
