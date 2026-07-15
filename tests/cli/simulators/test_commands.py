from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support.cli_runner import run_automa
from tests.support.fake_browser import fake_browser_environment, read_browser_calls
from tests.support.fake_simeval import fake_simeval_environment, read_simeval_calls


def _run_fake_forms(
    root: Path,
    command: str,
    *options: str,
    mode: str,
    browser: bool = False,
    check: bool = True,
) -> tuple[
    subprocess.CompletedProcess[str],
    subprocess.CompletedProcess[str],
    dict,
]:
    human_root = root / "human"
    machine_root = root / "machine"
    human_env = fake_simeval_environment(human_root, mode)
    machine_env = fake_simeval_environment(machine_root, mode)
    if browser:
        human_env = {**human_env, **fake_browser_environment(human_root)}
        machine_env = {**machine_env, **fake_browser_environment(machine_root)}

    human = run_automa(
        "simulators",
        command,
        *options,
        extra_env=human_env,
        check=check,
    )
    machine = run_automa(
        "simulators",
        command,
        *options,
        "--json",
        extra_env=machine_env,
        check=check,
    )
    return human, machine, json.loads(machine.stdout)


class SimulatorStatusTests(unittest.TestCase):
    def test_online_status_has_matching_human_and_json_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            human, machine, payload = _run_fake_forms(
                Path(tmp),
                "status",
                mode="online",
            )

        self.assertEqual(payload["schema"], "automa_simulator_status_v0")
        self.assertEqual(payload["result"]["status"], "ready")
        self.assertEqual(payload["result"]["reason_code"], "ready")
        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["status"]["online"])
        self.assertTrue(payload["frontend"]["frontend_connected"])
        self.assertIn("result: ready", human.stdout)
        self.assertIn("online: yes", human.stdout)
        self.assertIn("frontend tab connected: yes", human.stdout)
        self.assertNotIn("Commands:", human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")

    def test_offline_backend_status_has_matching_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            human, _, payload = _run_fake_forms(
                Path(tmp),
                "status",
                mode="launch_fails",
            )

        result = payload["result"]
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["reason_code"], "backend_offline")
        self.assertFalse(result["usable"])
        self.assertIn(result["error"], human.stdout)
        self.assertIn(result["recovery"], human.stdout)
        self.assertIn("online: no", human.stdout)
        self.assertNotIn("processAlive", human.stdout)

    def test_missing_frontend_status_has_matching_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            human, _, payload = _run_fake_forms(
                Path(tmp),
                "status",
                mode="online_no_frontend_then_open",
            )

        result = payload["result"]
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["reason_code"], "frontend_missing")
        self.assertFalse(result["usable"])
        self.assertIn(result["error"], human.stdout)
        self.assertIn(result["recovery"], human.stdout)
        self.assertIn("frontend tab connected: no", human.stdout)

    def test_missing_simeval_status_is_structured_and_actionable(self) -> None:
        env = {"PATH": "", "AUTOMA_SIMEVAL_BIN": ""}
        human = run_automa(
            "simulators",
            "status",
            extra_env=env,
            check=False,
        )
        machine = run_automa(
            "simulators",
            "status",
            "--json",
            extra_env=env,
            check=False,
        )

        payload = json.loads(machine.stdout)
        result = payload["result"]
        self.assertEqual(human.returncode, 2)
        self.assertEqual(machine.returncode, 2)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "simeval_missing")
        self.assertIn(result["error"], human.stdout)
        self.assertIn(result["recovery"], human.stdout)
        self.assertEqual(payload["commands"], [])
        self.assertNotIn("Traceback", human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")


class SimulatorEnsureTests(unittest.TestCase):
    def test_online_deployment_is_reused_with_quiet_human_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            human, _, payload = _run_fake_forms(root, "ensure", mode="online")
            calls = read_simeval_calls(root / "machine")

        result = payload["result"]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["reason_code"], "ready")
        self.assertTrue(result["usable"])
        self.assertFalse(result["launch_attempted"])
        self.assertFalse(result["launched"])
        self.assertNotIn(["deploy", "start"], calls)
        self.assertIn(["ui", "subapp", "--app", "play"], calls)
        self.assertIn("result: ready", human.stdout)
        self.assertIn("usable: yes", human.stdout)
        self.assertNotIn("Commands:", human.stdout)
        self.assertNotIn("processAlive", human.stdout)

    def test_requested_scenario_matches_human_json_and_simeval_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            human, _, payload = _run_fake_forms(
                root,
                "ensure",
                "--scenario",
                "chaser-depth-obstacles",
                mode="online",
            )
            calls = read_simeval_calls(root / "machine")

        self.assertTrue(payload["result"]["usable"])
        self.assertEqual(payload["desired"]["scenario"], "chaser-depth-obstacles")
        self.assertEqual(payload["scenario"]["scenario"], "chaser-depth-obstacles")
        self.assertIn(
            [
                "ui",
                "play-game-action",
                "--action-id",
                "scenario-select",
                "--value",
                '"chaser-depth-obstacles"',
            ],
            calls,
        )
        self.assertIn("scenario selected: chaser-depth-obstacles (yes)", human.stdout)

    def test_missing_frontend_opens_browser_and_becomes_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                **fake_simeval_environment(root, "online_no_frontend_then_open"),
                **fake_browser_environment(root),
            }
            machine = run_automa(
                "simulators",
                "ensure",
                "--json",
                extra_env=env,
            )
            payload = json.loads(machine.stdout)
            browser_calls = read_browser_calls(root)

        self.assertTrue(payload["result"]["usable"])
        self.assertEqual(payload["result"]["status"], "ready")
        self.assertTrue(payload["frontend"]["browser_open"]["attempted"])
        self.assertTrue(payload["frontend"]["after"]["frontend_connected"])
        self.assertEqual(browser_calls, ["http://127.0.0.1:5050"])

    def test_stale_frontend_is_reopened_before_play_setup_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                **fake_simeval_environment(root, "online_frontend_stale_until_open"),
                **fake_browser_environment(root),
            }
            machine = run_automa(
                "simulators",
                "ensure",
                "--json",
                extra_env=env,
            )
            payload = json.loads(machine.stdout)
            browser_calls = read_browser_calls(root)
            calls = read_simeval_calls(root)

        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["frontend"]["browser_open"]["attempted"])
        self.assertEqual(browser_calls, ["http://127.0.0.1:5050"])
        self.assertGreaterEqual(calls.count(["ui", "play-debug", "--summary"]), 2)

    def test_frontend_drop_is_concise_for_humans_and_complete_in_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            human, machine, payload = _run_fake_forms(
                Path(tmp),
                "ensure",
                mode="online_frontend_drops",
                check=False,
            )

        result = payload["result"]
        self.assertEqual(human.returncode, 2)
        self.assertEqual(machine.returncode, 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason_code"], "frontend_unstable")
        self.assertFalse(result["usable"])
        self.assertFalse(result["launch_attempted"])
        self.assertEqual(
            result["errors"],
            ["Chase frontend did not remain usable after setup"],
        )
        self.assertIn(result["error"], human.stdout)
        self.assertIn(result["recovery"], human.stdout)
        self.assertNotIn("Commands:", human.stdout)
        self.assertNotIn("Frontend disconnected after setup", human.stdout)
        self.assertTrue(
            any(
                "Frontend disconnected after setup" in command.get("stderr", "")
                for command in payload["commands"]
            )
        )

    def test_offline_backend_is_launched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = fake_simeval_environment(root, "offline_then_launch")
            machine = run_automa(
                "simulators",
                "ensure",
                "--json",
                extra_env=env,
            )
            payload = json.loads(machine.stdout)
            calls = read_simeval_calls(root)

        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["result"]["launch_attempted"])
        self.assertTrue(payload["result"]["launched"])
        self.assertIn(["deploy", "start"], calls)

    def test_launch_failure_has_one_human_problem_and_machine_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            human, machine, payload = _run_fake_forms(
                Path(tmp),
                "ensure",
                mode="launch_fails",
                check=False,
            )

        result = payload["result"]
        self.assertEqual(human.returncode, 2)
        self.assertEqual(machine.returncode, 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason_code"], "launch_failed")
        self.assertEqual(result["error"], "simeval deploy start failed")
        self.assertGreater(len(result["errors"]), 1)
        self.assertIn(result["error"], human.stdout)
        self.assertIn(result["recovery"], human.stdout)
        self.assertNotIn("Commands:", human.stdout)
        self.assertNotIn("processAlive", human.stdout)
        self.assertTrue(
            any(command.get("stderr") == "launch failed" for command in payload["commands"])
        )

    def test_missing_simeval_ensure_is_structured_and_actionable(self) -> None:
        env = {"PATH": "", "AUTOMA_SIMEVAL_BIN": ""}
        human = run_automa(
            "simulators",
            "ensure",
            extra_env=env,
            check=False,
        )
        machine = run_automa(
            "simulators",
            "ensure",
            "--json",
            extra_env=env,
            check=False,
        )

        payload = json.loads(machine.stdout)
        result = payload["result"]
        self.assertEqual(human.returncode, 2)
        self.assertEqual(machine.returncode, 2)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "simeval_missing")
        self.assertFalse(result["launch_attempted"])
        self.assertIn(result["error"], human.stdout)
        self.assertIn(result["recovery"], human.stdout)
        self.assertEqual(payload["commands"], [])
        self.assertNotIn("Traceback", human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
