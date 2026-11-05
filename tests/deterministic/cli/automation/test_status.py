from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.shared.cli.invoke import invoke_automa
from tests.support.runtime_fixtures import write_runtime_fixture, write_json


HOST_NAME = "chase-sim-chaser"
DEAD_PID = 999_999_999


class AutomationStatusTests(unittest.TestCase):

    def test_status_renderer_formats_supplied_hosts(self) -> None:
        """Text and JSON come from the supplied payload, including the release command."""

        from cli.automa_cli.hosts.runs.render import render_hosts_status

        self.assertEqual(render_hosts_status({"hosts": []}, json_output=False, long=False), "No hosts found")
        host = {
            "host_name": "car",
            "state": "not running",
            "run": {
                "host_name": "car",
                "deployed": False,
                "release": None,
                "steps": {},
                "applied_steps": {"available": False, "reason": "host not running"},
                "decision": {"activation": "bundle", "generation_id": None},
                "process": {"status": "not_started", "pid_state": "none"},
                "state": {},
                "published_view": {},
            },
        }
        status = {"schema": "automa_hosts_status", "host_name": "car", "hosts": [host]}
        text = render_hosts_status(status, json_output=False, long=True)
        self.assertIn("Host car  not running", text)
        self.assertIn("deployment: not deployed", text)
        self.assertIn("./cli/automa hosts update --name car", text)
        self.assertEqual(json.loads(render_hosts_status(status, json_output=True, long=False)), status)

    def test_top_level_status_prints_hosts_then_vehicles_and_their_exit(self) -> None:
        """``automa status`` prints hosts status, then vehicles status, and exits as vehicles status does."""

        from tests.support.fake_hosts import closed_url
        from tests.support.fake_metrics_ui import fake_metrics_ui

        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui() as server:
            runtime_root = Path(tmp) / "vehicles"
            runtime_root.mkdir()
            reachable = invoke_automa(
                "status", runtime_root=runtime_root,
                extra_env={"CHASE_UI_WS_URL": server.url}, check=False,
            )
            unreachable = invoke_automa(
                "status", runtime_root=runtime_root,
                extra_env={"CHASE_UI_WS_URL": closed_url()}, check=False,
            )

        self.assertEqual(reachable.returncode, 0, reachable.stderr)
        self.assertTrue(reachable.stdout.startswith("No hosts found\n\nSimulator (chase-sim)  running"), reachable.stdout)
        self.assertEqual(unreachable.returncode, 1, unreachable.stderr)
        self.assertTrue(unreachable.stdout.startswith("No hosts found\n\n"), unreachable.stdout)
        self.assertIn("unreachable", unreachable.stdout)
        self.assertEqual(unreachable.stderr, "")

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
