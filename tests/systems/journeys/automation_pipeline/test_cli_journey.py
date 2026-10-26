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
            from tests.support.runtime_fixtures import write_host_fixture
            write_host_fixture(runtime_root, "chase-sim-chaser", url=ws_url)
            invoke_automa(
                "vehicles", "update", "perception", "--id", "chase-sim-chaser",
                runtime_root=runtime_root, extra_env=env,
            )
            try:
                run_automa(
                    "vehicles", "automation", "run", "--id", "chase-sim-chaser",
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
                    [sys.executable, str(AUTOMA_PATH), "vehicles", "stream", "perception",
                     "--id", "chase-sim-chaser", "--json"],
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
                status = json.loads(invoke_automa(
                    "vehicles", "automation", "status", "--id", "chase-sim-chaser", "--json",
                    runtime_root=runtime_root, extra_env=env,
                ).stdout)
                self.assertTrue(status["vehicles"][0]["process"]["running"])
                self.assertEqual(status["vehicles"][0]["activation_problems"], [])
                self.assertFalse((runtime_root / "chase-sim-chaser/bundle/runtime/memory/active.json").exists())
            finally:
                invoke_automa(
                    "vehicles", "automation", "stop", "--id", "chase-sim-chaser",
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
                http_url = "http" + ws_url.removeprefix("ws").removesuffix(
                    "/ws/control"
                )

                def simulator() -> dict:
                    return json.loads(invoke_automa(
                        "vehicles", "status", "--chase-url", http_url, "--json",
                        runtime_root=runtime_root, extra_env=env,
                    ).stdout)

                initial = simulator()
                self.assertEqual((initial["state"], initial["hosts"]), ("running", []))
                initial_fingerprint = _session_fingerprint(ws_url)
                invoke_automa("vehicles", "create", "host", "--id", "chase-sim-chaser",
                              "--chase-url", ws_url, runtime_root=runtime_root, extra_env=env)

                invoke_automa(
                    "vehicles", "update", "memory", "--id", "chase-sim-chaser",
                    "--preset", "recency_ledger", runtime_root=runtime_root, extra_env=env,
                )

                invoke_automa(
                    "vehicles",
                    "update",
                    "perception",
                    "--id",
                    "chase-sim-chaser",
                    "--preset",
                    "lightweight_observer",
                    runtime_root=runtime_root,
                    extra_env=env,
                )
                staged = json.loads(invoke_automa(
                    "vehicles", "automation", "status", "--id", "chase-sim-chaser", "--json",
                    runtime_root=runtime_root, extra_env=env,
                ).stdout)["vehicles"][0]
                self.assertTrue(staged["deployed"])
                self.assertFalse(staged["process"]["running"])

                try:
                    run = run_automa(
                        "vehicles",
                        "automation",
                        "run",
                        "--id",
                        "chase-sim-chaser",
                        "--observe-only",
                        "--num-decisions",
                        "0",
                        "--open-view",
                        runtime_root=runtime_root,
                        extra_env=env,
                    )
                    self.assertIn("Automation ready", run.stdout)
                    self.assertIn(
                        "Ready for: inspect perception and stop automation",
                        run.stdout,
                    )

                    for step in ("perception", "memory"):
                        probe = json.loads(invoke_automa(
                            "vehicles", "stream", step, "--id", "chase-sim-chaser",
                            "--once", "--json", runtime_root=runtime_root, extra_env=env,
                        ).stdout)
                        self.assertEqual(probe["status"], "live")

                    old_run = json.loads(invoke_automa(
                        "vehicles", "automation", "status", "--id", "chase-sim-chaser",
                        "--json", runtime_root=runtime_root, extra_env=env,
                    ).stdout)["vehicles"][0]["state"]["run_id"]
                    restarted = run_automa(
                        "vehicles", "automation", "restart", "--id", "chase-sim-chaser",
                        "--observe-only", "--num-decisions", "0", "--open-view",
                        runtime_root=runtime_root, extra_env=env,
                    )
                    self.assertIn("Automation ready", restarted.stdout)
                    self.assertIn("Browser opened:", restarted.stdout)
                    new_run = json.loads(invoke_automa(
                        "vehicles", "automation", "status", "--id", "chase-sim-chaser",
                        "--json", runtime_root=runtime_root, extra_env=env,
                    ).stdout)["vehicles"][0]["state"]["run_id"]
                    self.assertNotEqual(new_run, old_run)

                    # The host record alone lists the host; wait until it attaches.
                    [host] = wait_until(
                        lambda: [
                            host for host in simulator()["hosts"]
                            if (host["vehicle_id"], host["attached"]) == ("chase-sim-chaser", True)
                        ],
                        message="the simulator never listed the running host as attached",
                    )
                    self.assertEqual((host["state"], host["mode"], host["error"]), ("running", "observe_only", None))
                    running = json.loads(invoke_automa(
                        "vehicles", "automation", "status", "--id", "chase-sim-chaser",
                        "--json", runtime_root=runtime_root, extra_env=env,
                    ).stdout)["vehicles"][0]
                    self.assertTrue(running["process"]["running"])
                    self.assertEqual(running["state"]["action_policy"], "observe_only")
                    self.assertEqual(running["state"]["control_application"], "not_applied")
                    self.assertEqual(_session_fingerprint(ws_url), initial_fingerprint)
                finally:
                    stopped = invoke_automa(
                        "vehicles",
                        "automation",
                        "stop",
                        "--id",
                        "chase-sim-chaser",
                        runtime_root=runtime_root,
                        extra_env=env,
                        check=False,
                    )

                self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                self.assertIn(
                    "Ready for: inspect stopped deployment",
                    stopped.stdout,
                )

                final = json.loads(invoke_automa(
                    "vehicles", "automation", "status", "--id", "chase-sim-chaser",
                    "--json", runtime_root=runtime_root, extra_env=env,
                ).stdout)["vehicles"][0]
                self.assertTrue(final["deployed"])
                self.assertFalse(final["process"]["running"])
                self.assertEqual(_session_fingerprint(ws_url), initial_fingerprint)
                deleted = invoke_automa(
                    "vehicles", "delete", "host", "--id", "chase-sim-chaser",
                    runtime_root=runtime_root, extra_env=env,
                )
                self.assertIn("Stopped host chase-sim-chaser", deleted.stdout)
                self.assertFalse((runtime_root / "chase-sim-chaser").exists())
                self.assertEqual(simulator()["hosts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
