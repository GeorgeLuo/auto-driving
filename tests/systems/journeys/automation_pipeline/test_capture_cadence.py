from __future__ import annotations

import itertools
import json
import tempfile
import time
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import stage_step, write_host_fixture
from unittest.mock import patch

from autonomy.runtime.client import RuntimeClient
from cli.automa_cli.hosts.local import stop_chase_host
from tests.support.cli_runner import run_automa
from tests.support import fake_metrics_ui
from tests.support.fake_metrics_ui import fake_metrics_ui_server


def control_protocol():
    """Acknowledge the simulator's control transport without touching a live UI."""
    read_response = fake_metrics_ui._response_for
    frames = itertools.count(1)

    def respond(message):
        kind = message.get("type")
        if kind in {"set_sidebar_app", "play", "play_game_command"}:
            return {"type": "ack", "request_id": message["request_id"], "payload": {"command": kind}}
        response = read_response(message)
        if kind == "get_play_debug":
            response["payload"]["actions"]["chaserInput"]["source"] = "ws"
            response["payload"]["frameIndex"] = next(frames)
        return response

    return patch("tests.support.fake_metrics_ui._response_for", side_effect=respond)


class ChaseCaptureCadenceTests(unittest.TestCase):


    def test_cli_num_decisions_limits_the_completed_shared_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            root = Path(tmp)
            env = {"CHASE_UI_WS_URL": ws_url}
            write_host_fixture(root, "chase-sim-chaser", url=ws_url)
            # Staging builds the controller release the car's runtime host runs.
            stage_step(root, "chase-sim-chaser", "perception", plugins=["frame"])
            automation = root / "chase-sim-chaser/bundle/runtime/automation"
            try:
                run = run_automa(
                    "hosts", "run", "--name", "chase-sim-chaser",
                    "--observe-only", "--num-decisions", "3", "--log",
                    runtime_root=root, extra_env=env,
                )
                # The background worker finishes the run record after the launch returns.
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    state = json.loads((automation / "state.json").read_text())
                    if state["status"] not in {"starting", "running"}:
                        break
                    time.sleep(.05)
            finally:
                with patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", root), patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", root):
                    stop_chase_host("chase-sim-chaser")
            self.assertEqual(state["status"], "completed", run.stdout)
            self.assertEqual(state["num_decisions"], 3)
            self.assertEqual(state["processed_count"], 3)
            self.assertEqual(state["session"]["configuration"]["num_decisions"], 3)
            self.assertEqual(state["session"]["processed_decisions"], 3)
            log = (automation / "automation.log").read_text()
            self.assertIn("Decisions: 3", log)
            self.assertIn("Decisions completed: 3", log)

    def test_unbounded_control_run_and_restart_override_the_count_until_explicit_stop(self):
        with (control_protocol(), tempfile.TemporaryDirectory() as tmp,
              fake_metrics_ui_server() as ws_url):
            root = Path(tmp)
            vehicle = "chase-sim-chaser"
            env = {"CHASE_UI_WS_URL": ws_url}
            write_host_fixture(root, "chase-sim-chaser", url=ws_url)
            stage_step(root, vehicle, "perception", plugins=["frame"])
            automation = root / vehicle / "bundle/runtime/automation"
            try:
                for verb in ("run", "restart"):
                    run = run_automa(
                        "hosts", verb, "--name", vehicle,
                        "--unbounded", "--num-decisions", "1",
                        runtime_root=root, extra_env=env,
                    )
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    record = json.loads((automation / "host.json").read_text())
                    client = RuntimeClient(record["base_url"], timeout_s=3)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        status = client.status()
                        if status["session"]["processed_decisions"] >= 3:
                            break
                        time.sleep(.05)
                    session = status["session"]
                    self.assertEqual(session["status"], "running")
                    self.assertGreaterEqual(session["processed_decisions"], 3)
                    self.assertIsNone(session["configuration"]["num_decisions"])
                    self.assertEqual(session["configuration"]["mode"], "autonomy")
                    state = json.loads((automation / "state.json").read_text())
                    self.assertIsNone(state["num_decisions"])
                    self.assertEqual(state["action_policy"], "autonomy")
                run_automa("hosts", "stop", "--name", vehicle,
                           runtime_root=root, extra_env=env)
                session = client.status()["session"]
                self.assertEqual(session["status"], "stopped")
                self.assertEqual(session["execution"]["mode"], "manual")
                self.assertEqual(client.status()["host_run_id"], record["host_run_id"])
                state = json.loads((automation / "state.json").read_text())
                self.assertEqual(state["status"], "stopped")
                self.assertGreaterEqual(state["processed_count"], 3)
            finally:
                run_automa("hosts", "stop", "--name", vehicle,
                           runtime_root=root, extra_env=env, check=False)
                with patch("cli.automa_cli.hosts.staging.RUNTIME_ROOT", root), patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", root):
                    stop_chase_host(vehicle)
