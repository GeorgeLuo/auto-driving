from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support.cli_runner import run_automa
from tests.support.runtime_fixtures import write_json, write_runtime_fixture


VEHICLE_ID = "chase-sim-chaser"
DEAD_PID = 999_999_999


def _set_runtime_state(path: Path, **changes: object) -> None:
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(changes)
    write_json(path, state)


class AutomationStatusTests(unittest.TestCase):
    def test_empty_runtime_has_matching_human_and_json_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            human = run_automa("vehicles", "automation", "status", runtime_root=runtime_root)
            machine = run_automa(
                "vehicles",
                "automation",
                "status",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(machine.stdout)
        self.assertEqual(payload["schema"], "automa_automation_status_v0")
        self.assertEqual(payload["outcome"]["status"], "empty")
        self.assertEqual(payload["vehicles"], [])
        self.assertIn("deployed automations: 0", human.stdout)
        self.assertIn(payload["outcome"]["message"], human.stdout)
        self.assertIn(payload["outcome"]["recovery"], human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")

    def test_unknown_runtime_is_actionable_in_human_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            human = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "missing-car",
                runtime_root=runtime_root,
                check=False,
            )
            machine = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "missing-car",
                "--json",
                runtime_root=runtime_root,
                check=False,
            )

        payload = json.loads(machine.stdout)
        self.assertEqual(human.returncode, 2)
        self.assertEqual(machine.returncode, 2)
        self.assertEqual(payload["outcome"]["status"], "not_found")
        self.assertEqual(payload["requested_vehicle_id"], "missing-car")
        self.assertEqual(payload["vehicles"], [])
        self.assertIn(payload["outcome"]["message"], human.stdout)
        self.assertIn(payload["outcome"]["expected_bundle"], human.stdout)
        self.assertIn(payload["outcome"]["recovery"], human.stdout)
        self.assertNotIn("Traceback", human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")

    def test_running_runtime_has_matching_human_and_json_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, VEHICLE_ID, pid=os.getpid())

            human = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                VEHICLE_ID,
                runtime_root=runtime_root,
            )
            machine = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                VEHICLE_ID,
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(machine.stdout)
        self.assertEqual(payload["schema"], "automa_automation_status_v0")
        self.assertEqual(payload["outcome"]["status"], "ok")
        self.assertEqual(len(payload["vehicles"]), 1)
        vehicle = payload["vehicles"][0]
        self.assertEqual(vehicle["vehicle_id"], VEHICLE_ID)
        self.assertTrue(vehicle["deployed"])
        self.assertEqual(vehicle["perception"]["algorithm"], "sim_debug")
        self.assertEqual(vehicle["decision"]["engine_id"], "idle")
        self.assertEqual(vehicle["process"]["status"], "running")
        self.assertTrue(vehicle["process"]["running"])

        self.assertIn("deployed automations: 1", human.stdout)
        self.assertIn(vehicle["vehicle_id"], human.stdout)
        self.assertIn(f"perception: {vehicle['perception']['algorithm']}", human.stdout)
        self.assertIn(f"decision: {vehicle['decision']['engine_id']}", human.stdout)
        self.assertIn(f"worker: {vehicle['process']['status']}", human.stdout)
        self.assertIn("log: disabled", human.stdout)
        self.assertNotIn("engine_config", human.stdout)
        self.assertNotIn("mapper_config", human.stdout)
        self.assertNotIn("Traceback", human.stdout)

    def test_stale_worker_is_explicit_and_actionable_in_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, VEHICLE_ID, pid=DEAD_PID)

            human = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                VEHICLE_ID,
                runtime_root=runtime_root,
            )
            machine = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                VEHICLE_ID,
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(machine.stdout)
        self.assertEqual(payload["outcome"]["status"], "degraded")
        process = payload["vehicles"][0]["process"]
        self.assertFalse(process["running"])
        self.assertEqual(process["pid_state"], "not_running")
        self.assertEqual(process["status"], "stale")
        self.assertIn(str(DEAD_PID), process["reason"])
        self.assertIn(VEHICLE_ID, process["recovery"])
        self.assertIn("worker: stale", human.stdout)
        self.assertIn(process["reason"], human.stdout)
        self.assertIn(process["recovery"], human.stdout)
        self.assertNotIn("worker: running", human.stdout)

    def test_error_state_is_actionable_without_human_traceback_noise(self) -> None:
        full_error = "camera capture failed\nTraceback (most recent call last): internal detail"
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            paths = write_runtime_fixture(runtime_root, VEHICLE_ID, pid=DEAD_PID)
            _set_runtime_state(
                paths.automation_state,
                status="error",
                error=full_error,
                exit_code=2,
            )

            human = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                VEHICLE_ID,
                runtime_root=runtime_root,
            )
            machine = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                VEHICLE_ID,
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(machine.stdout)
        self.assertEqual(payload["outcome"]["status"], "degraded")
        vehicle = payload["vehicles"][0]
        process = vehicle["process"]
        self.assertEqual(process["status"], "error")
        self.assertEqual(process["reason"], "camera capture failed")
        self.assertIn(VEHICLE_ID, process["recovery"])
        self.assertEqual(vehicle["state"]["error"], full_error)
        self.assertEqual(vehicle["state"]["exit_code"], 2)
        self.assertIn("worker: error", human.stdout)
        self.assertIn("problem: camera capture failed", human.stdout)
        self.assertIn(process["recovery"], human.stdout)
        self.assertNotIn("Traceback", human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")

    def test_scenario_stop_stale_worker_marks_runtime_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, VEHICLE_ID, pid=DEAD_PID)

            stop = run_automa(
                "vehicles",
                "automation",
                "stop",
                "--id",
                VEHICLE_ID,
                runtime_root=runtime_root,
            )
            status = run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                VEHICLE_ID,
                "--json",
                runtime_root=runtime_root,
            )

        self.assertIn("Automation is not running", stop.stdout)
        payload = json.loads(status.stdout)
        state = payload["vehicles"][0]["state"]
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(payload["vehicles"][0]["process"]["status"], "stopped")

    def test_view_warmup_polling_is_bounded_by_command_budget(self) -> None:
        """view_timeout_s is one wall-clock budget for probes and warm-up sleeps."""

        from cli.automa_cli.automation import _collect_automation_status

        warming = {
            "schema": "automa_perception_view_v1",
            "available": False,
            "status": "warming",
            "url": "http://127.0.0.1:8898/",
            "reason": "waiting for first correlated frame",
        }
        probe_calls: list[float] = []

        def _probe(_automation_dir, *, timeout_s, **_kwargs):
            probe_calls.append(float(timeout_s))
            time.sleep(min(0.02, max(0.0, float(timeout_s))))
            return dict(warming)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, VEHICLE_ID, pid=os.getpid())
            with mock.patch(
                "cli.automa_cli.automation.RUNTIME_ROOT",
                runtime_root,
            ), mock.patch(
                "cli.automa_cli.automation.get_perception_view_status",
                side_effect=_probe,
            ):
                started = time.monotonic()
                statuses = _collect_automation_status(
                    vehicle_id=VEHICLE_ID,
                    view_timeout_s=0.05,
                )
                elapsed = time.monotonic() - started

        self.assertEqual(len(statuses), 1)
        self.assertLess(
            elapsed,
            0.35,
            f"view warm-up exceeded remaining budget: elapsed={elapsed:.3f}s "
            f"probes={probe_calls}",
        )
        self.assertTrue(probe_calls)
        self.assertTrue(all(timeout <= 0.05 + 1e-9 for timeout in probe_calls))
        self.assertFalse(statuses[0]["published_view"]["available"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class AutomationRunIdTests(unittest.TestCase):
    def test_now_id_is_unique_within_same_wall_clock_second(self) -> None:
        from datetime import datetime
        from unittest import mock

        from cli.automa_cli.automation import _now_id

        frozen = datetime(2026, 7, 26, 12, 0, 0)
        with mock.patch("cli.automa_cli.automation.datetime") as mocked:
            mocked.now.return_value = frozen
            first = _now_id("automation")
            second = _now_id("automation")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("automation-20260726-120000-"))
        self.assertTrue(second.startswith("automation-20260726-120000-"))
        # Trailing UUID fragments must both be present and distinct.
        self.assertEqual(len(first.split("-")), 4)
        self.assertNotEqual(first.rsplit("-", 1)[-1], second.rsplit("-", 1)[-1])
