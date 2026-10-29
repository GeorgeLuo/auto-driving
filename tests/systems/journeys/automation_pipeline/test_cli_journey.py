from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from implementations.vehicle.chase_sim.car import ChaseSimCar
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
from tests.shared.cli.invoke import invoke_automa
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.cli_runner import AUTOMA_PATH, WORKSPACE_ROOT, run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server


def _session_fingerprint(ws_url: str) -> dict:
    passive = ChaseSimCar(ws_url=ws_url, timeout_s=5.0).inspect_passive_capture()
    preservation = passive["session_preservation"]
    if passive["status"] != "available" or preservation["preserved"] is not True:
        raise AssertionError(f"passive session is not proven: {passive}")
    if passive["mutation_attempted"] is not False:
        raise AssertionError(f"passive capture attempted a mutation: {passive}")
    if preservation["before"] != preservation["after"]:
        raise AssertionError(f"passive capture changed the session: {preservation}")
    # The simulator plays between captures, so only its frame index moves.
    session = dict(preservation["before"])
    session["playback"] = {key: value for key, value in session["playback"].items() if key != "frameIndex"}
    return session


class SimulatorPerceptionCliJourneyTests(unittest.TestCase):
    def test_interrupting_a_terminal_stream_does_not_stop_the_background_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            env = {"CHASE_UI_WS_URL": ws_url}
            from tests.support.runtime_fixtures import stage_step, write_host_fixture
            write_host_fixture(runtime_root, "chase-sim-chaser", url=ws_url)
            stage_step(runtime_root, "chase-sim-chaser", "perception")
            try:
                run_automa(
                    "hosts", "run", "--name", "chase-sim-chaser",
                    "--observe-only", "--num-decisions", "0", runtime_root=runtime_root, extra_env=env,
                )
                stream_env = {
                    **os.environ, **env,
                    "AUTOMA_RUNTIME_ROOT": str(runtime_root),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": os.pathsep.join(filter(None, (
                        str(WORKSPACE_ROOT / "tests/support/offline"),
                        os.environ.get("PYTHONPATH"),
                    ))),
                }
                with subprocess.Popen(
                    [sys.executable, str(AUTOMA_PATH), "plugins", "stream", "--step", "perception",
                     "--host-name", "chase-sim-chaser", "--json"],
                    cwd=WORKSPACE_ROOT, env=stream_env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ) as stream:
                    try:
                        with selectors.DefaultSelector() as reader:
                            reader.register(stream.stdout, selectors.EVENT_READ)
                            self.assertTrue(reader.select(timeout=10), "stream did not publish")
                            self.assertEqual(json.loads(stream.stdout.readline())["status"], "live")
                        stream.send_signal(signal.SIGINT)
                        _, stderr = stream.communicate(timeout=10)
                        self.assertEqual(stream.returncode, 130)
                        self.assertEqual(stderr, "")
                    finally:
                        if stream.poll() is None:
                            stream.terminate()
                            stream.wait(timeout=5)
                run = json.loads(invoke_automa(
                    "hosts", "status", "--name", "chase-sim-chaser", "--json",
                    runtime_root=runtime_root, extra_env=env,
                ).stdout)["hosts"][0]["run"]
                self.assertTrue(run["process"]["running"])
                self.assertEqual(run["activation_problems"], [])
                self.assertFalse((runtime_root / "chase-sim-chaser/bundle/runtime/memory/active.json").exists())
            finally:
                invoke_automa(
                    "hosts", "stop", "--name", "chase-sim-chaser",
                    runtime_root=runtime_root, extra_env=env, check=False,
                )

    def test_primary_journey_uses_shared_passive_gates_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with fake_metrics_ui_server() as ws_url:
                env = {
                    "BROWSER": "true",
                    "CHASE_UI_WS_URL": ws_url,
                }

                def simulator() -> dict:
                    return json.loads(invoke_automa(
                        "vehicles", "status", "--json", runtime_root=runtime_root, extra_env=env,
                    ).stdout)

                initial = simulator()
                self.assertEqual([vehicle["state"] for vehicle in initial["vehicles"]], ["running"])
                self.assertNotIn("hosts", initial)
                initial_fingerprint = _session_fingerprint(ws_url)
                invoke_automa("hosts", "create", "--name", "chase-sim-chaser", "--provider", "chase-sim",
                              "--url", ws_url, runtime_root=runtime_root, extra_env=env)
                # Creating the host stages every step's default.
                staged = json.loads(invoke_automa(
                    "hosts", "status", "--name", "chase-sim-chaser", "--json",
                    runtime_root=runtime_root, extra_env=env,
                ).stdout)["hosts"][0]["run"]
                self.assertTrue(staged["deployed"])
                self.assertFalse(staged["process"]["running"])

                try:
                    run = run_automa(
                        "hosts",
                        "run",
                        "--name",
                        "chase-sim-chaser",
                        "--observe-only",
                        "--num-decisions",
                        "0",
                        "--open-view",
                        runtime_root=runtime_root,
                        extra_env=env,
                    )
                    self.assertIn("Run started on host", run.stdout)
                    self.assertNotIn("ready for:", run.stdout.lower())

                    for step in ("perception", "memory"):
                        probe = json.loads(invoke_automa(
                            "plugins", "stream", "--step", step, "--host-name", "chase-sim-chaser",
                            "--once", "--json", runtime_root=runtime_root, extra_env=env,
                        ).stdout)
                        self.assertEqual(probe["status"], "live")

                    old_run = json.loads(invoke_automa(
                        "hosts", "status", "--name", "chase-sim-chaser",
                        "--json", runtime_root=runtime_root, extra_env=env,
                    ).stdout)["hosts"][0]["run"]["state"]["run_id"]
                    restarted = run_automa(
                        "hosts", "restart", "--name", "chase-sim-chaser",
                        "--observe-only", "--num-decisions", "0", "--open-view",
                        runtime_root=runtime_root, extra_env=env,
                    )
                    self.assertIn("Run started on host", restarted.stdout)
                    self.assertIn("Browser opened:", restarted.stdout)
                    new_run = json.loads(invoke_automa(
                        "hosts", "status", "--name", "chase-sim-chaser",
                        "--json", runtime_root=runtime_root, extra_env=env,
                    ).stdout)["hosts"][0]["run"]["state"]["run_id"]
                    self.assertNotEqual(new_run, old_run)

                    # The runtime announces its host identity to the simulator.
                    agents = MetricsUiWsClient(ws_url)
                    [presence] = wait_until(
                        lambda: [agent for agent in agents.list_agents()
                                 if (agent.get("client") or {}).get("host_name") == "chase-sim-chaser"],
                        message="the simulator never listed the running host as attached",
                    )
                    self.assertEqual(presence["client"]["kind"], "host")
                    running = json.loads(invoke_automa(
                        "hosts", "status", "--name", "chase-sim-chaser",
                        "--json", runtime_root=runtime_root, extra_env=env,
                    ).stdout)["hosts"][0]["run"]
                    self.assertTrue(running["process"]["running"])
                    self.assertEqual(running["state"]["action_policy"], "observe_only")
                    self.assertEqual(running["state"]["control_application"], "not_applied")
                    self.assertEqual(_session_fingerprint(ws_url), initial_fingerprint)
                finally:
                    stopped = invoke_automa(
                        "hosts",
                        "stop",
                        "--name",
                        "chase-sim-chaser",
                        runtime_root=runtime_root,
                        extra_env=env,
                        check=False,
                    )

                self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                self.assertNotIn("ready for:", stopped.stdout.lower())

                final = json.loads(invoke_automa(
                    "hosts", "status", "--name", "chase-sim-chaser",
                    "--json", runtime_root=runtime_root, extra_env=env,
                ).stdout)["hosts"][0]["run"]
                self.assertTrue(final["deployed"])
                self.assertFalse(final["process"]["running"])
                self.assertEqual(_session_fingerprint(ws_url), initial_fingerprint)
                deleted = invoke_automa(
                    "hosts", "delete", "--name", "chase-sim-chaser",
                    runtime_root=runtime_root, extra_env=env,
                )
                self.assertIn("Stopped host chase-sim-chaser", deleted.stdout)
                self.assertFalse((runtime_root / "chase-sim-chaser").exists())
                self.assertNotIn("hosts", simulator())
                self.assertEqual(json.loads(invoke_automa(
                    "hosts", "status", "--json", runtime_root=runtime_root, extra_env=env,
                ).stdout)["hosts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
