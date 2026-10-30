from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.shared.cli.invoke import invoke_automa
from tests.support.runtime_fixtures import write_runtime_fixture, write_json


HOST_NAME = "chase-sim-chaser"
DEAD_PID = 999_999_999


class AutomationStatusTests(unittest.TestCase):

    def test_view_warmup_polling_is_bounded_by_command_budget(self) -> None:
        """view_timeout_s is one wall-clock budget for probes and warm-up sleeps."""

        from cli.automa_cli.hosts.automation_status import collect_automation_status

        warming = {
            "schema": "automa_perception_view",
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
            write_runtime_fixture(runtime_root, HOST_NAME, pid=os.getpid())
            with mock.patch(
                "cli.automa_cli.hosts.runtime_hosts.RUNTIME_ROOT",
                runtime_root,
            ), mock.patch(
                "cli.automa_cli.shared.controller_releases.RUNTIME_ROOT",
                runtime_root,
            ), mock.patch(
                "cli.automa_cli.hosts.automation_status.get_perception_view_status",
                side_effect=_probe,
            ):
                started = time.monotonic()
                statuses = collect_automation_status(
                    host_name=HOST_NAME,
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

    def test_aggregate_view_probes_share_one_command_deadline(self) -> None:
        """Aggregate status must not restart view_timeout_s for each runtime."""

        from cli.automa_cli.hosts.automation_status import collect_automation_status

        warming = {
            "schema": "automa_perception_view",
            "available": False,
            "status": "warming",
            "url": "http://127.0.0.1:8898/",
            "reason": "waiting for first correlated frame",
        }
        probe_calls: list[float] = []

        def _probe(_automation_dir, *, timeout_s, **_kwargs):
            probe_calls.append(float(timeout_s))
            time.sleep(min(0.05, max(0.0, float(timeout_s))))
            return dict(warming)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "chase-sim-chaser", pid=os.getpid())
            write_runtime_fixture(runtime_root, "chase-sim-runner", pid=os.getpid())
            with mock.patch(
                "cli.automa_cli.hosts.runtime_hosts.RUNTIME_ROOT",
                runtime_root,
            ), mock.patch(
                "cli.automa_cli.shared.controller_releases.RUNTIME_ROOT",
                runtime_root,
            ), mock.patch(
                "cli.automa_cli.hosts.automation_status.get_perception_view_status",
                side_effect=_probe,
            ):
                started = time.monotonic()
                statuses = collect_automation_status(
                    host_name=None,
                    view_timeout_s=0.2,
                )
                elapsed = time.monotonic() - started

        self.assertEqual(len(statuses), 2)
        self.assertLess(
            elapsed,
            0.35,
            f"aggregate view probes exceeded one shared budget: elapsed={elapsed:.3f}s "
            f"probes={probe_calls}",
        )
        # Two independent 0.2s budgets would allow ~0.4s+; shared budget must not.
        self.assertLess(elapsed, 0.4)
        self.assertTrue(probe_calls)
        self.assertTrue(all(timeout <= 0.2 + 1e-9 for timeout in probe_calls))

    def test_no_hosts_are_found_in_human_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            human = invoke_automa("hosts", "status", "--long", runtime_root=runtime_root)
            machine = invoke_automa("hosts", "status", "--json", runtime_root=runtime_root)

        self.assertEqual(json.loads(machine.stdout), {"schema": "automa_hosts_status", "host_name": None, "hosts": []})
        self.assertEqual(human.stdout, "No hosts found\n")
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")

    def test_an_unknown_id_is_not_found_and_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            human = invoke_automa("hosts", "status", "--name", "missing-car", runtime_root=runtime_root, check=False)
            machine = invoke_automa(
                "hosts", "status", "--name", "missing-car", "--json", runtime_root=runtime_root, check=False,
            )

        payload = json.loads(machine.stdout)
        self.assertEqual((human.returncode, machine.returncode), (1, 1))
        self.assertEqual(payload["host_name"], "missing-car")
        [host] = payload["hosts"]
        self.assertEqual((host["host_name"], host["state"], host["run"]), ("missing-car", "not found", None))
        self.assertEqual(human.stdout, "Host missing-car  not found\n")
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")

    def test_a_running_worker_has_matching_human_and_json_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, HOST_NAME, pid=os.getpid())

            human = invoke_automa("hosts", "status", "--long", runtime_root=runtime_root)
            machine = invoke_automa("hosts", "status", "--json", runtime_root=runtime_root)

        payload = json.loads(machine.stdout)
        self.assertEqual(payload["schema"], "automa_hosts_status")
        [host] = payload["hosts"]
        self.assertEqual(host["host_name"], HOST_NAME)
        self.assertEqual(host["state"], "not running")
        run = host["run"]
        self.assertTrue(run["deployed"])
        steps = run["steps"]
        self.assertEqual(
            {step: item["source"] for step, item in steps.items()},
            {"observation": "builtin", "perception": "staged", "memory": "none",
             "proposal": "none", "plan": "none", "action": "builtin"},
        )
        self.assertEqual(steps["perception"]["preset"], "sim_debug")
        self.assertEqual(steps["action"]["plugins"], ["selected"])
        self.assertEqual(run["process"]["status"], "running")
        self.assertTrue(run["process"]["running"])

        # The host block comes first, then the host's run.
        self.assertTrue(human.stdout.startswith(
            f"Host {HOST_NAME}  not running\n"
            "  chaser at http://localhost:5050/\n"
            "  local host record\n"
            "  deployment: deployed\n"
            f"  release: {run['release']['tree_sha256']}\n"
            "  steps: staged; applied unavailable (the run has not reported its steps)\n"
        ), human.stdout)
        self.assertEqual(run["applied_steps"], {"available": False, "reason": "the run has not reported its steps"})
        self.assertIn(f"    perception: {steps['perception']['preset']} (", human.stdout)
        self.assertIn("    memory: (no plugins)  source=none\n", human.stdout)
        self.assertIn("    plan: (no plugins)  source=none\n", human.stdout)
        self.assertIn("    action: selected  source=builtin\n", human.stdout)
        self.assertIn(f"  decision: {run['decision']['generation_id']}\n", human.stdout)
        self.assertIn(f"worker: {run['process']['status']}", human.stdout)
        self.assertIn("log: disabled", human.stdout)
        self.assertNotIn("plugin_configs", human.stdout)
        self.assertNotIn("Traceback", human.stdout)

    def test_a_stale_worker_says_why_without_next_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, HOST_NAME, pid=DEAD_PID)

            human = invoke_automa("hosts", "status", "--long", runtime_root=runtime_root)
            machine = invoke_automa("hosts", "status", "--json", runtime_root=runtime_root)

        process = json.loads(machine.stdout)["hosts"][0]["run"]["process"]
        self.assertFalse(process["running"])
        self.assertEqual(process["pid_state"], "not_running")
        self.assertEqual(process["status"], "stale")
        self.assertIn(str(DEAD_PID), process["reason"])
        self.assertNotIn("recovery", process)
        self.assertIn("worker: stale", human.stdout)
        self.assertIn(f"  problem: {process['reason']}\n", human.stdout)
        self.assertNotIn("worker: running", human.stdout)
        for hint in ("next:", "Next:", "Expected bundle:"):
            self.assertNotIn(hint, human.stdout)

    def test_an_error_state_says_why_without_human_traceback_noise(self) -> None:
        full_error = "camera capture failed\nTraceback (most recent call last): internal detail"
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            paths = write_runtime_fixture(runtime_root, HOST_NAME, pid=DEAD_PID)
            _set_runtime_state(
                paths.automation_state,
                status="error",
                error=full_error,
                exit_code=2,
            )

            human = invoke_automa("hosts", "status", "--long", runtime_root=runtime_root)
            machine = invoke_automa("hosts", "status", "--json", runtime_root=runtime_root)

        run = json.loads(machine.stdout)["hosts"][0]["run"]
        process = run["process"]
        self.assertEqual(process["status"], "error")
        self.assertEqual(process["reason"], "camera capture failed")
        self.assertEqual(run["state"]["error"], full_error)
        self.assertEqual(run["state"]["exit_code"], 2)
        self.assertIn("worker: error", human.stdout)
        self.assertIn("problem: camera capture failed", human.stdout)
        self.assertNotIn("next:", human.stdout)
        self.assertNotIn("Traceback", human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")

    def test_scenario_stop_stale_worker_marks_runtime_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, HOST_NAME, pid=DEAD_PID)

            stop = invoke_automa(
                "hosts",
                "stop",
                "--name",
                HOST_NAME,
                runtime_root=runtime_root,
            )
            status = invoke_automa("hosts", "status", "--json", runtime_root=runtime_root)

        self.assertIn("has no run", stop.stdout)
        run = json.loads(status.stdout)["hosts"][0]["run"]
        self.assertEqual(run["state"]["status"], "stopped")
        self.assertEqual(run["process"]["status"], "stopped")


def _set_runtime_state(path: Path, **changes: object) -> None:
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(changes)
    write_json(path, state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
