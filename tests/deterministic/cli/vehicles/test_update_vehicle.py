from __future__ import annotations

import contextlib
import json
import tempfile
import threading
import time
import unittest
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from autonomy.decision_cycle.activation import STEPS
from cli.automa_cli.step_activations import (
    bundle_activation_path,
    read_bundle_activation,
    staging_vehicle,
    vehicle_bundle,
)
from implementations.decision_cycle.catalog import SELECTABLE_STEPS, STEP_PRESETS
from tests.shared.cli.invoke import invoke_automa

DISCOVERY = "cli.automa_cli.vehicles.discover_active_vehicles"
PICAR = {
    "vehicle_id": "piracer-test",
    "vehicle_kind": "picar",
    "provider": "picar",
    "connection": {"base_url": "http://piracer-test.local:8887", "source": "test"},
}


def _invoke(*args: str) -> tuple[int, str]:
    result = invoke_automa(*args, check=False)
    return result.returncode, result.stdout


def _discovered(*vehicles: dict) -> dict:
    return {"active_count": len(vehicles), "vehicles": list(vehicles), "inactive": []}


class UpdateVehicleTests(unittest.TestCase):
    """Every `vehicles update <step>` stages only for a vehicle it can name."""

    @contextlib.contextmanager
    def _runtime(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("cli.automa_cli.app.VEHICLES_RUNTIME_ROOT", root),
            ):
                yield root

    def test_every_step_rejects_an_id_discovery_does_not_find(self) -> None:
        for step in STEPS:
            with (
                self.subTest(step=step),
                self._runtime() as root,
                patch(DISCOVERY, return_value=_discovered()) as discover,
            ):
                code, stdout = _invoke(
                    "vehicles", "update", step, "--id", "parity-unknown", "--timeout-s", "0.5"
                )
                self.assertEqual(code, 2, stdout)
                self.assertIn(
                    "Vehicle 'parity-unknown' was not found among discoverable vehicles.", stdout
                )
                self.assertEqual(discover.call_args.kwargs["timeout_s"], 0.5)
                self.assertEqual(list(root.iterdir()), [])

    def test_generic_step_reports_an_unknown_vehicle_in_its_json_error(self) -> None:
        with self._runtime(), patch(DISCOVERY, return_value=_discovered()):
            code, stdout = _invoke("vehicles", "update", "proposal", "--id", "parity-unknown", "--json")
        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertEqual(payload["schema"], "vehicle_step_update_error_v0")
        self.assertEqual(payload["error"], "unknown_vehicle")
        self.assertIn("parity-unknown", payload["message"])

    def test_sim_ids_and_vehicles_with_staged_perception_need_no_discovery(self) -> None:
        with self._runtime():
            with patch(DISCOVERY, return_value=_discovered(PICAR)) as discover:
                code, stdout = _invoke("vehicles", "update", "perception", "--id", "piracer-test", "--json")
            self.assertEqual(code, 0, stdout)
            discover.assert_called_once()

            offline = MagicMock(side_effect=AssertionError("staging must not discover"))
            for vehicle_id in ("chase-sim-chaser", "piracer-test"):
                for step in STEPS:
                    with self.subTest(vehicle_id=vehicle_id, step=step), patch(DISCOVERY, offline):
                        code, stdout = _invoke(
                            "vehicles", "update", step, "--id", vehicle_id, "--dry-run"
                        )
                        self.assertEqual(code, 0, stdout)
            offline.assert_not_called()

    def test_a_plugin_list_keeps_staged_configs_and_a_config_change_names_its_restart(self) -> None:
        # The PiCar case: a tuned preset, then one more plugin, must stay adoptable live.
        plugins = ("frame", "floor_plane", "multi_obstruction_tracks", "motion_tracks")
        with self._runtime() as root, patch(DISCOVERY, return_value=_discovered(PICAR)):
            def update(*args: str) -> str:
                code, stdout = _invoke("vehicles", "update", "perception", "--id", "piracer-test", *args)
                self.assertEqual(code, 0, stdout)
                return stdout

            update("--preset", "obstruction_observer")
            tuned = read_bundle_activation(vehicle_bundle("piracer-test", root), "perception").plugin_configs
            added = json.loads(update(*(arg for plugin in plugins for arg in ("--plugin", plugin)), "--json"))
            self.assertEqual(added["manifest"]["plugins"], list(plugins))
            self.assertEqual(added["manifest"]["plugin_configs"], tuned)
            self.assertEqual(added["apply"]["changed_plugins"], [])
            self.assertEqual(added["apply"]["selection_command"],
                             "./cli/automa vehicles update autonomy --id piracer-test")

            switched = update("--preset", "visual_observer")
            self.assertIn(
                "Apply: ./cli/automa vehicles update autonomy --id piracer-test --restart "
                "(specs or configs changed: multi_obstruction_tracks)",
                switched,
            )
            self.assertNotIn("Apply selection:", switched)

    def test_a_one_plugin_step_restaged_to_another_packaged_plugin_applies_live(self) -> None:
        with self._runtime(), patch(DISCOVERY, return_value=_discovered(PICAR)):
            def update(*args: str) -> str:
                code, stdout = _invoke("vehicles", "update", "action", "--id", "piracer-test", *args)
                self.assertEqual(code, 0, stdout)
                return stdout

            update()
            apply = json.loads(update("--plugin", "hold", "--json"))["apply"]
            self.assertEqual((apply["live_selection"], apply["changed_plugins"]), (True, []))
            self.assertEqual(apply["selection_command"], "./cli/automa vehicles update autonomy --id piracer-test")
            self.assertIn("Apply selection: ./cli/automa vehicles update autonomy --id piracer-test\n",
                          update("--plugin", "selected"))

    def test_each_first_staged_step_makes_the_vehicle_known_to_every_step_offline(self) -> None:
        for first in STEPS:
            with self.subTest(first=first), self._runtime() as root:
                with patch(DISCOVERY, return_value=_discovered(PICAR)) as discover:
                    code, stdout = _invoke(
                        "vehicles", "update", first, "--id", PICAR["vehicle_id"], "--json"
                    )
                self.assertEqual(code, 0, stdout)
                discover.assert_called_once()
                bundle = vehicle_bundle(PICAR["vehicle_id"], root)
                activation = read_bundle_activation(bundle, first)
                self.assertEqual(activation.metadata["vehicle_id"], PICAR["vehicle_id"])
                self.assertEqual(activation.metadata.get("provider"), PICAR["provider"])
                self.assertEqual(
                    activation.metadata.get("runtime", {}).get("connection"), PICAR["connection"]
                )
                before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                with patch(DISCOVERY, side_effect=AssertionError("offline staging must not discover")):
                    for step in STEPS:
                        with self.subTest(first=first, step=step):
                            code, stdout = _invoke(
                                "vehicles", "update", step, "--id", PICAR["vehicle_id"],
                                "--dry-run", "--json",
                            )
                            self.assertEqual(code, 0, stdout)
                            self.assertTrue(json.loads(stdout)["dry_run"])
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_a_staged_identity_does_not_validate_another_id_with_the_same_path(self) -> None:
        with self._runtime() as root:
            with patch(DISCOVERY, return_value=_discovered(PICAR)):
                code, stdout = _invoke("vehicles", "update", "perception", "--id", "piracer-test")
            self.assertEqual(code, 0, stdout)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            for step in STEPS:
                with self.subTest(step=step), patch(DISCOVERY, return_value=_discovered()) as discover:
                    code, stdout = _invoke(
                        "vehicles", "update", step, "--id", "piracer test", "--json"
                    )
                self.assertEqual(code, 2, stdout)
                discover.assert_called_once()
                self.assertEqual(json.loads(stdout)["error"], "unknown_vehicle")
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_offline_resolution_uses_the_newest_matching_valid_identity(self) -> None:
        from implementations.decision_cycle.catalog import packaged_activation

        with self._runtime() as root:
            bundle = vehicle_bundle(PICAR["vehicle_id"], root)
            for step, vehicle_id, timestamp, url in (
                ("perception", PICAR["vehicle_id"], 1, "http://old.local:8887"),
                ("memory", PICAR["vehicle_id"], 2, "http://new.local:8887"),
                ("proposal", "other-pi", 3, "http://wrong.local:8887"),
            ):
                payload = packaged_activation(step).to_payload()
                payload["metadata"] = {
                    "vehicle_id": vehicle_id, "provider": "picar", "vehicle_kind": "picar",
                    "activated_at_ms": timestamp, "runtime": {"connection": {"base_url": url}},
                }
                path = bundle_activation_path(bundle, step)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload))
            bad = bundle_activation_path(bundle, "plan")
            bad.parent.mkdir(parents=True)
            bad.write_text("{broken json")
            with patch(DISCOVERY, side_effect=AssertionError("matching metadata is known offline")):
                vehicle, error = staging_vehicle(PICAR["vehicle_id"], runtime_root=root)
            self.assertIsNone(error)
            self.assertEqual(vehicle["connection"]["base_url"], "http://new.local:8887")
            # A legacy perception identity without a timestamp remains usable.
            bundle_activation_path(bundle, "memory").unlink()
            path = bundle_activation_path(bundle, "perception")
            payload = json.loads(path.read_text())
            payload["metadata"].pop("activated_at_ms")
            path.write_text(json.dumps(payload))
            with patch(DISCOVERY, side_effect=AssertionError("legacy metadata is known offline")):
                vehicle, error = staging_vehicle(PICAR["vehicle_id"], runtime_root=root)
            self.assertIsNone(error)
            self.assertEqual(vehicle["connection"]["base_url"], "http://old.local:8887")

    def test_staged_chase_endpoint_wins_over_the_offline_default(self) -> None:
        from implementations.decision_cycle.catalog import packaged_activation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vehicle, error = staging_vehicle("chase-sim-chaser", runtime_root=root)
            self.assertIsNone(error)
            self.assertEqual(vehicle["connection"]["source"], "offline-default")
            bundle = vehicle_bundle("chase-sim-chaser", root)
            payload = packaged_activation("perception").to_payload()
            payload["metadata"] = {
                "vehicle_id": "chase-sim-chaser",
                "provider": "chase-sim",
                "vehicle_kind": "chase-sim",
                "activated_at_ms": 5,
                "runtime": {
                    "connection": {
                        "ws_url": "ws://127.0.0.1:5099/ws/control",
                        "source": "connection",
                    }
                },
            }
            path = bundle_activation_path(bundle, "perception")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
            vehicle, error = staging_vehicle("chase-sim-chaser", runtime_root=root)
            self.assertIsNone(error)
            self.assertEqual(vehicle["connection"]["ws_url"], "ws://127.0.0.1:5099/ws/control")

    def test_unknown_vehicle_json_is_transferable_between_all_step_commands(self) -> None:
        for step in STEPS:
            with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = invoke_automa(
                    "vehicles", "update", step, "--id", "parity-unknown",
                    "--timeout-s", "0.05", "--json", runtime_root=root, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["schema"], "vehicle_step_update_error_v0")
                self.assertEqual(payload["vehicle_id"], "parity-unknown")
                self.assertEqual(payload["step"], step)
                self.assertEqual(payload["error"], "unknown_vehicle")
                self.assertEqual(result.stderr, "")
                self.assertEqual(list(root.iterdir()), [])

    def test_every_selectable_step_parses_its_selection_into_the_shared_update(self) -> None:
        """Regression: perception, memory and proposal each wired their own
        selection arguments to their own update path."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for step in SELECTABLE_STEPS:
                preset = sorted(STEP_PRESETS[step])[-1]
                with self.subTest(step=step):
                    payload = json.loads(invoke_automa(
                        "vehicles", "update", step, "--id", "chase-sim-chaser",
                        "--preset", preset, "--dry-run", "--json", runtime_root=root,
                    ).stdout)
                    self.assertEqual(payload["schema"], "vehicle_step_update_v0")
                    self.assertEqual((payload["step"], payload["preset"]), (step, preset))
            self.assertEqual(list(root.iterdir()), [])

    def test_physical_memory_staging_supports_offline_cli_updates_of_every_step(self) -> None:
        # Exercise the public executable and real discovery HTTP boundary.
        requests = []

        class StatusHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append(self.path)
                body = b'{"ok": true, "mode": "user"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = {
                    "PIRACER_ID": "parity-pi",
                    "PIRACER_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                }
                staged = invoke_automa(
                    "vehicles", "update", "memory", "--id", "parity-pi",
                    "--timeout-s", "0.1", "--json", runtime_root=root, extra_env=env,
                )
                self.assertEqual(json.loads(staged.stdout)["plugins"], ["bounded_evidence"])
                server.shutdown()
                server.server_close()
                worker.join(2)
                before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                for step in STEPS:
                    with self.subTest(step=step):
                        result = invoke_automa(
                            "vehicles", "update", step, "--id", "parity-pi",
                            "--timeout-s", "0.1", "--dry-run", "--json",
                            runtime_root=root, extra_env=env,
                        )
                        self.assertTrue(json.loads(result.stdout)["dry_run"])
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.assertEqual(requests, ["/autonomy/status"])

    def test_a_slow_host_is_reported_with_the_wait_not_only_as_not_found(self) -> None:
        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                time.sleep(1.0)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = invoke_automa(
                    "vehicles", "update", "perception", "--id", "slow-pi", "--timeout-s", "0.2",
                    runtime_root=Path(tmp), extra_env={"PIRACER_ID": "slow-pi", "PIRACER_BASE_URL": base_url},
                    check=False,
                )
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("Vehicle 'slow-pi' was not found among discoverable vehicles.", result.stdout)
        self.assertIn(f"GET {base_url}/autonomy/status did not answer within 0.2 s", result.stdout)

    def test_help_explains_the_shared_identity_rule(self) -> None:
        for step in STEPS:
            with self.subTest(step=step):
                help_text = " ".join(invoke_automa("vehicles", "update", step, "--help").stdout.split())
                self.assertIn("matching identity metadata in any staged step", help_text)
                self.assertIn("for each vehicle discovery probe", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
