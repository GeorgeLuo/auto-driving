"""One unreadable run record is a recoverable command error, not a traceback."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.hosts.deployment import service as deployment
from cli.automa_cli.hosts.runs import service
from cli.automa_cli.hosts.runs.models import RunState, WorkerProcess
from tests.shared.cli.invoke import invoke_automa
from tests.support.runtime_fixtures import write_runtime_fixture


class UnreadableRunRecordTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = write_runtime_fixture(self.root, "car", pid=42)
        self.automation = paths.automation_state.parent
        self.enterContext(patch.object(service, "running_base_url", return_value=None))
        self.enterContext(patch.object(deployment, "running_base_url", return_value=None))
        self.enterContext(patch.object(service, "bundle_activation_problems", return_value=[]))
        self.launch = self.enterContext(patch.object(service, "launch_worker"))
        self.stop = self.enterContext(patch.object(service, "stop_worker", return_value="stopped"))
        self.stop_host = self.enterContext(patch.object(deployment, "stop_chase_host"))

    def invalid_records(self):
        for name, record in (("process.json", WorkerProcess(pid=42)), ("state.json", RunState(pid=42))):
            document = record.to_dict()
            document["pid"] = True
            for text in ("{", "null", json.dumps(document)):
                # Both files start valid, so a state error cannot hide behind a process error.
                (self.automation / "process.json").write_text(json.dumps(WorkerProcess(pid=42).to_dict()))
                (self.automation / "state.json").write_text(json.dumps(RunState(pid=42).to_dict()))
                path = self.automation / name
                path.write_text(text)
                yield path, text

    def assert_recovery(self, result, path):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(str(path), result.stdout)
        self.assertIn("once no worker is running", result.stdout)
        self.assertIn(f"remove {path} and retry", result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_run_reports_invalid_records_without_launching_or_overwriting(self):
        # An invalid state is rejected even if no live process can reuse it.
        with patch.object(service, "pid_alive", return_value=False):
            for path, text in self.invalid_records():
                with self.subTest(path=path.name, text=text):
                    result = invoke_automa("hosts", "run", "--name", "car", "--observe-only",
                                           runtime_root=self.root, check=False)
                    self.assert_recovery(result, path)
                    self.assertEqual(path.read_text(), text)
        self.launch.assert_not_called()

    def test_stop_reports_invalid_records_without_claiming_or_signalling(self):
        for path, text in self.invalid_records():
            with self.subTest(path=path.name, text=text):
                result = invoke_automa("hosts", "stop", "--name", "car", runtime_root=self.root, check=False)
                self.assert_recovery(result, path)
                self.assertEqual(path.read_text(), text)
                self.assertFalse((self.automation / "order.json").exists())
        self.stop.assert_not_called()

    def test_status_keeps_other_hosts_when_a_run_record_is_unreadable(self):
        write_runtime_fixture(self.root, "other", pid=999_999_999)
        for path, text in self.invalid_records():
            with self.subTest(path=path.name, text=text):
                result = invoke_automa("hosts", "status", "--json", runtime_root=self.root)
                hosts = {host["host_name"]: host for host in json.loads(result.stdout)["hosts"]}
                unreadable = hosts["car"]["run"]
                self.assertEqual(unreadable["status"], "unreadable")
                self.assertIn(str(path), unreadable["error"])
                self.assertIn("once no worker is running", unreadable["error"])
                self.assertIn("process", hosts["other"]["run"])
                human = invoke_automa("hosts", "status", "--long", runtime_root=self.root)
                self.assertIn("run: unreadable", human.stdout)
                self.assertIn(str(path), human.stdout)
                self.assertIn("Host other", human.stdout)
                named = invoke_automa("hosts", "status", "--name", "car", "--json",
                                      runtime_root=self.root, check=False)
                self.assertEqual(named.returncode, 1)

    def test_deployment_stop_reports_invalid_records_and_keeps_the_host(self):
        for path, text in self.invalid_records():
            with self.subTest(path=path.name, text=text):
                # _stop_active_automation is the deployment helper reached by hosts delete.
                result = invoke_automa("hosts", "delete", "--name", "car", runtime_root=self.root, check=False)
                self.assert_recovery(result, path)
                self.assertEqual(path.read_text(), text)
                self.assertTrue((self.root / "car" / "host-configuration.json").exists())
        self.stop_host.assert_not_called()
        self.stop.assert_not_called()

    def test_stop_accepts_main_5f0cf136_records_and_rewrites_the_complete_shape(self):
        process = {
            "schema": "automa_automation_process", "host_name": "car", "pid": 42, "started_at_ms": 1000,
            "command": ["python", "-m", "cli.automa_cli.hosts.runs.worker"], "log_to_disk": False,
            "log_path": None, "state_path": "runtime/state.json", "stream_command": "automa hosts stream",
        }
        state = {
            "schema": "automa_automation_run_state", "host_name": "car", "run_id": "run-1",
            "status": "running", "pid": 42, "started_at_ms": 1000, "updated_at_ms": 1100,
            "frames_received": 2, "frames_dropped": 0, "processed_count": 1, "num_decisions": 3,
            "pipeline": "latest_frame_async_decision", "control_source": "runtime_host",
            "action_policy": "observe_only", "control_application": "not_applied", "recording": False,
            "recorded_count": 0, "run_dir": None,
        }
        (self.automation / "process.json").write_text(json.dumps(process))
        (self.automation / "state.json").write_text(json.dumps(state))
        with patch.object(service, "pid_alive", return_value=True), \
             patch.object(service, "pid_matches_automation", return_value=True):
            result = invoke_automa("hosts", "stop", "--name", "car", runtime_root=self.root)
        self.stop.assert_called_once_with(42, wait_s=3.0)
        self.assertEqual(result.returncode, 0)
        written_process = WorkerProcess.from_dict(json.loads((self.automation / "process.json").read_text()))
        written_state = RunState.from_dict(json.loads((self.automation / "state.json").read_text()))
        self.assertEqual(written_process.stopped_by, "stop_command")
        self.assertEqual(written_state.status, "stopped")
