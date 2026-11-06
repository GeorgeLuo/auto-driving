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
from urllib.error import HTTPError
from urllib.request import urlopen

from implementations.vehicle.chase_sim.car import ChaseSimCar
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
from tests.shared.cli.invoke import invoke_automa
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.cli_runner import AUTOMA_PATH, WORKSPACE_ROOT, run_automa, start_automa
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


def _latest(view_url: str) -> dict | None:
    """The view's latest publication, or ``None`` before it has one."""

    try:
        with urlopen(f"{view_url.rstrip('/')}/api/latest", timeout=5) as response:
            return json.load(response)
    except HTTPError:
        return None


def _read_line(process: subprocess.Popen, *, timeout_s: float = 10.0) -> str:
    with selectors.DefaultSelector() as reader:
        reader.register(process.stdout, selectors.EVENT_READ)
        if not reader.select(timeout=timeout_s):
            raise AssertionError("the command printed nothing")
        return process.stdout.readline()


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
                    "--observe-only", runtime_root=runtime_root, extra_env=env,
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

                discovered = invoke_automa(
                    "vehicles", "discover", "--provider", "chase-sim", "--url", ws_url,
                    runtime_root=runtime_root, extra_env=env,
                )
                self.assertEqual(discovered.returncode, 0, discovered.stdout)
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
                        runtime_root=runtime_root,
                        extra_env=env,
                    )
                    self.assertIn("Run started on host", run.stdout)
                    self.assertIn("View: ./cli/automa hosts viewer --name chase-sim-chaser", run.stdout)
                    self.assertNotIn("Browser", run.stdout)
                    self.assertNotIn("ready for:", run.stdout.lower())

                    # Viewing is explicit: the viewer serves the pages until Ctrl-C ends it, not the run.
                    with start_automa("hosts", "viewer", "--name", "chase-sim-chaser",
                                      runtime_root=runtime_root, extra_env=env) as viewer:
                        try:
                            self.assertTrue(_read_line(viewer).startswith("Viewing host chase-sim-chaser at "))
                            # The viewer prints its notice as one block; the rest is buffered already.
                            browser = viewer.stdout.readline()
                            self.assertTrue(browser.startswith("Browser opened: "), browser)
                            view_url = browser.removeprefix("Browser opened: ").strip()
                            with urlopen(f"{view_url.rstrip('/')}/perception", timeout=5) as page:
                                self.assertEqual(page.status, 200)
                            latest = wait_until(lambda: _latest(view_url), message="the viewer published no frame")
                            self.assertTrue(latest["report"]["frame_id"])
                            self.assertTrue(latest["image"]["url"])
                            shown = json.loads(invoke_automa(
                                "hosts", "status", "--name", "chase-sim-chaser", "--json",
                                runtime_root=runtime_root, extra_env=env,
                            ).stdout)["hosts"][0]["run"]
                            self.assertTrue(shown["process"]["running"])
                            self.assertEqual(shown["published_view"]["url"], view_url)
                            self.assertTrue(shown["published_view"]["available"])
                            viewer.send_signal(signal.SIGINT)
                            self.assertEqual(viewer.wait(timeout=10), 130)
                        finally:
                            if viewer.poll() is None:
                                viewer.terminate()
                                viewer.wait(timeout=5)
                    after_view = json.loads(invoke_automa(
                        "hosts", "status", "--name", "chase-sim-chaser", "--json",
                        runtime_root=runtime_root, extra_env=env,
                    ).stdout)["hosts"][0]["run"]
                    self.assertTrue(after_view["process"]["running"])
                    self.assertEqual(after_view["state"]["session"]["status"], "running")
                    self.assertFalse(after_view["published_view"]["available"])

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
                        "--observe-only",
                        runtime_root=runtime_root, extra_env=env,
                    )
                    self.assertIn("Run started on host", restarted.stdout)
                    self.assertNotIn("Browser", restarted.stdout)
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
