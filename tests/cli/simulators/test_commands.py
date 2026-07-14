from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support.cli_runner import run_automa
from tests.support.fake_browser import fake_browser_environment, read_browser_calls
from tests.support.fake_simeval import fake_simeval_environment, read_simeval_calls


class SimulatorCommandTests(unittest.TestCase):
    def test_simulator_status_json_online(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = fake_simeval_environment(Path(tmp), "online")
            result = run_automa("simulators", "status", "--json", extra_env=env)

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_simulator_status_v0")
        self.assertTrue(payload["status"]["online"])
        self.assertEqual(payload["status"]["online_count"], 1)
        self.assertTrue(payload["frontend"]["frontend_connected"])

    def test_simulator_ensure_reuses_online_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = fake_simeval_environment(root, "online")
            result = run_automa("simulators", "ensure", "--json", extra_env=env)
            calls = read_simeval_calls(root)

        payload = json.loads(result.stdout)
        self.assertTrue(payload["result"]["usable"])
        self.assertFalse(payload["result"]["launched"])
        self.assertNotIn(["deploy", "start"], calls)
        self.assertIn(["ui", "subapp", "--app", "play"], calls)
        self.assertIn(
            ["ui", "play-game-action", "--action-id", "scenario-select", "--value", '"default"'],
            calls,
        )

    def test_simulator_ensure_selects_requested_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = fake_simeval_environment(root, "online")
            result = run_automa(
                "simulators",
                "ensure",
                "--scenario",
                "chaser-depth-obstacles",
                "--json",
                extra_env=env,
            )
            calls = read_simeval_calls(root)

            payload = json.loads(result.stdout)
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

            text_result = run_automa(
                "simulators",
                "ensure",
                "--scenario",
                "chaser-depth-obstacles",
                extra_env=env,
            )
            self.assertIn("scenario selected: chaser-depth-obstacles (yes)", text_result.stdout)

    def test_simulator_ensure_opens_browser_when_frontend_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                **fake_simeval_environment(root, "online_no_frontend_then_open"),
                **fake_browser_environment(root),
            }
            result = run_automa("simulators", "ensure", "--json", extra_env=env)
            browser_calls = read_browser_calls(root)

        payload = json.loads(result.stdout)
        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["frontend"]["browser_open"]["attempted"])
        self.assertTrue(payload["frontend"]["after"]["frontend_connected"])
        self.assertEqual(browser_calls, ["http://127.0.0.1:5050"])

    def test_simulator_ensure_reopens_stale_frontend_when_play_commands_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                **fake_simeval_environment(root, "online_frontend_stale_until_open"),
                **fake_browser_environment(root),
            }
            result = run_automa("simulators", "ensure", "--json", extra_env=env)
            browser_calls = read_browser_calls(root)
            calls = read_simeval_calls(root)

        payload = json.loads(result.stdout)
        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["frontend"]["browser_open"]["attempted"])
        self.assertEqual(browser_calls, ["http://127.0.0.1:5050"])
        self.assertGreaterEqual(calls.count(["ui", "play-debug", "--summary"]), 2)

    def test_simulator_ensure_rejects_frontend_that_drops_after_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = fake_simeval_environment(Path(tmp), "online_frontend_drops")
            result = run_automa("simulators", "ensure", "--json", extra_env=env, check=False)

        payload = json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(payload["result"]["usable"])
        self.assertFalse(payload["stability"]["ok"])
        self.assertIn("did not remain usable", payload["result"]["error"])

    def test_simulator_ensure_launches_when_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = fake_simeval_environment(root, "offline_then_launch")
            result = run_automa("simulators", "ensure", "--json", extra_env=env)
            calls = read_simeval_calls(root)

        payload = json.loads(result.stdout)
        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["result"]["launched"])
        self.assertIn(["deploy", "start"], calls)

    def test_simulator_ensure_reports_launch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = fake_simeval_environment(Path(tmp), "launch_fails")
            result = run_automa("simulators", "ensure", extra_env=env, check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launch attempted: yes", result.stdout)
        self.assertIn("launched: no", result.stdout)
        self.assertIn("usable: no", result.stdout)
        self.assertIn("simeval deploy start failed", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
