"""Real native executable through the unchanged Chase adapter and public CLI.

Build simulator/native first. These tests never use a Metrics UI checkout or browser.
"""
from __future__ import annotations

import base64
import json
import socket
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.vehicle import VehicleAction
from cli.automa_cli.simulators.native import NATIVE_SIM_BINARY, NATIVE_SIM_DIR
from implementations.vehicle.chase_sim.car import ChaseSimCar
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiConnection, MetricsUiWsClient, MetricsUiWebSocketError
from tests.shared.cli.invoke import invoke_automa
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.cli_runner import run_automa


@unittest.skipUnless(NATIVE_SIM_BINARY.is_file(), "build simulator/native to test the native backend")
class NativeSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/ws/control"
        # Creation is in-process only to assign an ephemeral port; it starts the real C++ process.
        with patch("cli.automa_cli.simulators.service.SIMULATOR_PORTS", [port]):
            invoke_automa("simulators", "create", "--backend", "native", runtime_root=self.root,
                          extra_env={"AUTOMA_METRICS_UI_DIR": ""})
        self.addCleanup(self.cli, "simulators", "delete", "--url", self.url, check=False)
        self.client = MetricsUiWsClient(self.url, timeout_s=5)
        self.car = ChaseSimCar(ws_url=self.url, timeout_s=5)

    def cli(self, *args: str, check: bool = True):
        return invoke_automa(*args, runtime_root=self.root, check=check,
                             extra_env={"AUTOMA_METRICS_UI_DIR": ""})

    def native(self):
        return self.client.play_game_query("native-state")

    def test_custom_map_file_is_used_at_startup_and_reported_by_cli(self) -> None:
        config = json.loads((NATIVE_SIM_DIR / "maps/chaser-depth-obstacles.json").read_text())
        config["scenarioId"] = "custom-startup"
        config["materials"]["floor"] = [70, 80, 90]
        path = self.root / "custom-map.json"
        path.write_text(json.dumps(config))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"ws://127.0.0.1:{port}/ws/control"
        with patch("cli.automa_cli.simulators.service.SIMULATOR_PORTS", [port]):
            invoke_automa("simulators", "create", "--backend", "native", "--config", str(path),
                          runtime_root=self.root, extra_env={"AUTOMA_METRICS_UI_DIR": ""})
        self.addCleanup(self.cli, "simulators", "delete", "--url", url)
        state = MetricsUiWsClient(url).play_game_query("native-state")
        self.assertEqual(state["settings"], config)
        status = json.loads(self.cli("simulators", "status", "--url", url, "--json").stdout)
        self.assertEqual(status["simulators"][0]["config"], str(path.resolve()))

    def test_cli_host_observation_and_control_runs_complete_and_detach(self) -> None:
        status = json.loads(self.cli("simulators", "status", "--url", self.url, "--json").stdout)
        self.assertEqual(status["simulators"][0]["state"], "running")
        self.assertEqual(status["simulators"][0]["backend"], "native")
        self.assertTrue(self.car.inspect_passive_capture()["session_preservation"]["preserved"])
        self.cli("vehicles", "discover", "--provider", "chase-sim", "--url", self.url)
        self.cli("hosts", "create", "--name", "native", "--provider", "chase-sim", "--url", self.url)
        self.addCleanup(self.cli, "hosts", "delete", "--name", "native", check=False)
        wait_until(lambda: any(agent.get("client", {}).get("host_name") == "native"
                              for agent in self.client.list_agents()))
        state_path = self.root / "native/bundle/runtime/automation/state.json"

        def completed(previous_start: int = -1):
            try:
                state = json.loads(state_path.read_text())
            except (OSError, ValueError):
                return None
            if state.get("status") in {"failed", "error"}:
                self.fail(json.dumps(state, indent=2))
            if state.get("status") != "completed":
                return None
            if state.get("started_at_ms", 0) <= previous_start:
                return None
            return state

        for mode in (("--observe-only",), ()):
            previous = completed()
            previous_start = previous["started_at_ms"] if previous else -1
            run_automa("hosts", "run", "--name", "native", "--num-decisions", "6", "--timeout-s", "10", *mode,
                       runtime_root=self.root, extra_env={"AUTOMA_METRICS_UI_DIR": ""})
            try:
                state = wait_until(lambda: completed(previous_start), timeout_s=15)
            except AssertionError:
                self.fail(self.cli("hosts", "status", "--name", "native", "--long", check=False).stdout
                          + (state_path.read_text() if state_path.exists() else str(state_path) + " does not exist"))
            self.assertEqual(state["processed_count"], 6)
            self.assertEqual(state["stop_reason"], "completed")
            self.assertEqual(state["control_application"], "not_applied" if mode else "shared_execution")
            self.assertGreaterEqual(state["frames_received"], 6)
            self.cli("hosts", "stop", "--name", "native")
        report = json.loads(self.cli("hosts", "status", "--name", "native", "--json").stdout)
        # Host status counts both completed runs; each run record above counts six.
        self.assertEqual(report["hosts"][0]["run"]["state"]["processed_count"], 12)
        refused = self.cli("simulators", "delete", "--url", self.url, check=False)
        self.assertEqual(refused.returncode, 2)
        self.cli("hosts", "delete", "--name", "native")
        wait_until(lambda: self.client.list_agents() == [])
        self.cli("simulators", "delete", "--url", self.url)
        self.assertEqual(json.loads(self.cli("simulators", "status", "--json").stdout)["simulators"], [])

    def test_passive_capture_steering_and_pause_preserve_real_state(self) -> None:
        self.client.command({"type": "pause"}, wait_for_frontend_ack=True)
        before = self.native()
        passive = self.car.inspect_passive_capture()
        self.assertEqual(passive["status"], "available")
        self.assertEqual(self.native(), before)
        image = self.client.play_game_query("atomic-evaluation-capture")["sensor"]["image"]
        decoded = Image.open(BytesIO(base64.b64decode(image["dataUrl"].split(",", 1)[1])))
        self.assertEqual(decoded.size, (640, 480))
        self.assertGreater(max(decoded.convert("L").getextrema()) - min(decoded.convert("L").getextrema()), 40)
        # Automa's right-positive steering is inverted by its existing Chase adapter.
        self.car.execute_action(VehicleAction(forward=True, steering=0.4), throttle=0.5)
        self.client.play_game_command("step", {"frames": 10})
        moved = self.native()
        self.assertGreater(moved["pose"]["x"], before["pose"]["x"])
        self.assertLess(moved["pose"]["z"], before["pose"]["z"])
        self.car.stop()
        self.client.play_game_command("step", {"frames": 10})
        self.assertEqual(self.native()["pose"], moved["pose"])

    def test_live_configuration_is_revisioned_and_reset_ends_stream(self) -> None:
        with MetricsUiConnection(self.url, timeout_s=5) as wire:
            wire.send_json({"type": "register", "role": "agent"})
            self.client.wait_for_registration(wire, time.monotonic() + 5)
            wire.send_json({"type": "play_camera_stream_unsubscribe", "subscriptionId": ""})
            error = wire.recv_json()
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["payload"]["code"], "invalid_request")
            wire.send_json({"type": "play_camera_stream_subscribe", "width": 160, "height": 120,
                            "maxRateHz": 30, "imageFormat": "image/jpeg", "dropPolicy": "latest-frame"})
            first = wire.recv_json()["payload"]
            self.assertEqual(first["event"], "subscribed")
            baseline = first["frame"]
            self.client.play_game_command("configure", {"vehicle": {"speedUnitsPerFrame": 0.06},
                                                        "materials": {"floor": [20, 180, 40], "seed": 8}})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                update = wire.recv_json(timeout_s=2)["payload"]
                if update.get("configurationRevision") == 1:
                    break
            else:
                self.fail("no frame from the new configuration")
            self.assertEqual(update["frameIdentity"]["simulationEpoch"], baseline["frameIdentity"]["simulationEpoch"])
            self.assertGreater(update["frameIdentity"]["frameIndex"], baseline["frameIdentity"]["frameIndex"])
            self.assertNotEqual(update["sensor"]["image"]["dataUrl"], baseline["sensor"]["image"]["dataUrl"])
            self.client.play_game_command("reset", {})
            while time.monotonic() < deadline:
                terminal = wire.recv_json(timeout_s=2)
                if terminal["type"] == "play_camera_stream_result":
                    break
            self.assertEqual(terminal["payload"]["event"], "ended")
            self.assertEqual(terminal["payload"]["reason"]["code"], "session_changed")

    def test_map_replacement_and_invalid_commands_leave_no_partial_changes(self) -> None:
        with self.assertRaises(MetricsUiWebSocketError):
            self.client.play_game_command("configure", {"vehicle": {"wheelbase": 0}})
        self.assertEqual(self.native()["configurationRevision"], 0)
        self.client.command({"type": "pause"}, wait_for_frontend_ack=True)
        before = self.native()
        with self.assertRaises(MetricsUiWebSocketError):
            self.client.play_game_command("set-chaser-input", {"motion": "fly"})
        with self.assertRaises(MetricsUiWebSocketError):
            self.client.play_game_query("unknown-query")
        self.assertEqual(self.native(), before)
        config = json.loads((NATIVE_SIM_DIR / "maps/chaser-depth-obstacles.json").read_text())
        config["scenarioId"] = "new-map"
        config["map"]["obstacles"] = []
        self.client.play_game_command("load-map", config)
        after = self.native()
        self.assertNotEqual(after["frameIdentity"]["simulationEpoch"], before["frameIdentity"]["simulationEpoch"])
        self.assertEqual(after["frameIdentity"]["frameIndex"], 0)
        self.assertEqual(after["settings"]["scenarioId"], "new-map")
        self.assertFalse(after["playing"])
        self.assertTrue(self.car.inspect_passive_capture()["session_preservation"]["preserved"])
