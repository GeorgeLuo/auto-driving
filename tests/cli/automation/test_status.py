from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.support.cli_runner import run_automa
from tests.support.runtime_fixtures import write_runtime_fixture


class AutomationStatusTests(unittest.TestCase):
    def test_automation_status_empty_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = run_automa("vehicles", "automation", "status", runtime_root=runtime_root)

        self.assertIn("deployed automations: 0", result.stdout)
        self.assertIn("No deployed automation runtimes found.", result.stdout)

    def test_automation_status_reads_fake_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "chase-sim-chaser", pid=os.getpid())

            result = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "chase-sim-chaser",
                runtime_root=runtime_root,
            )

        self.assertIn("deployed automations: 1", result.stdout)
        self.assertIn("chase-sim-chaser", result.stdout)
        self.assertIn("perception: sim_debug", result.stdout)
        self.assertIn("worker: running", result.stdout)
        self.assertIn("log: disabled", result.stdout)

    def test_scenario_status_distinguishes_stale_worker_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "chase-sim-chaser", pid=999_999_999)

            result = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        process = payload["vehicles"][0]["process"]
        self.assertFalse(process["running"])
        self.assertEqual(process["pid_state"], "not_running")

    def test_scenario_stop_stale_worker_marks_runtime_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "chase-sim-chaser", pid=999_999_999)

            stop = run_automa(
                "vehicles",
                "automation",
                "stop",
                "--id",
                "chase-sim-chaser",
                runtime_root=runtime_root,
            )
            status = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        self.assertIn("Automation is not running", stop.stdout)
        payload = json.loads(status.stdout)
        state = payload["vehicles"][0]["state"]
        self.assertEqual(state["status"], "stopped")

    def test_automation_status_json_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "chase-sim-chaser", pid=os.getpid())

            result = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_automation_status_v0")
        self.assertEqual(len(payload["vehicles"]), 1)
        vehicle = payload["vehicles"][0]
        self.assertEqual(vehicle["vehicle_id"], "chase-sim-chaser")
        self.assertTrue(vehicle["deployed"])
        self.assertEqual(vehicle["perception"]["algorithm"], "sim_debug")
        self.assertEqual(vehicle["decision"]["engine_id"], "idle")
        self.assertTrue(vehicle["process"]["running"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
