from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.activation import STEPS
from cli.automa_cli.step_activations import (
    read_bundle_activation,
    staging_vehicle,
    write_host_vehicle,
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
            write_host_vehicle(root, PICAR)
            with (
                patch("cli.automa_cli.app.VEHICLES_RUNTIME_ROOT", root),
            ):
                yield root


    def test_generic_step_reports_an_unknown_vehicle_in_its_json_error(self) -> None:
        with self._runtime(), patch(DISCOVERY, return_value=_discovered()):
            code, stdout = _invoke("vehicles", "update", "proposal", "--id", "parity-unknown", "--json")
        self.assertEqual(code, 2)
        payload = json.loads(stdout)
        self.assertEqual(payload["schema"], "vehicle_step_update_error_v0")
        self.assertEqual(payload["error"], "unknown_vehicle")
        self.assertIn("parity-unknown", payload["message"])


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
                             "./cli/automa vehicles host update --id piracer-test")

            switched = update("--preset", "visual_observer")
            self.assertIn(
                "Apply: ./cli/automa vehicles host update --id piracer-test --restart "
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
            self.assertEqual(apply["selection_command"], "./cli/automa vehicles host update --id piracer-test")
            self.assertIn("Apply selection: ./cli/automa vehicles host update --id piracer-test\n",
                          update("--plugin", "selected"))


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
            write_host_vehicle(root, {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim",
                "connection": {"ws_url": "ws://localhost:5050/ws/control"}})
            for step in SELECTABLE_STEPS:
                preset = sorted(STEP_PRESETS[step])[-1]
                with self.subTest(step=step):
                    payload = json.loads(invoke_automa(
                        "vehicles", "update", step, "--id", "chase-sim-chaser",
                        "--preset", preset, "--dry-run", "--json", runtime_root=root,
                    ).stdout)
                    self.assertEqual(payload["schema"], "vehicle_step_update_v0")
                    self.assertEqual((payload["step"], payload["preset"]), (step, preset))
            self.assertEqual(len(list(root.rglob("vehicle.json"))), 1)



if __name__ == "__main__":
    unittest.main(verbosity=2)

    def test_prefixes_and_staged_steps_do_not_create_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chase-sim-test" / "bundle").mkdir(parents=True)
            for name in ("chase-sim-test", "unknown"):
                vehicle, error = staging_vehicle(name, runtime_root=root)
                self.assertIsNone(vehicle)
                self.assertEqual(error, f"No host {name!r}. No hosts exist.")

    def test_record_controls_identity_and_connection_without_discovery(self):
        with self._runtime() as root, patch(DISCOVERY, side_effect=AssertionError("no discovery")):
            vehicle, error = staging_vehicle("piracer-test", runtime_root=root)
            self.assertIsNone(error)
            self.assertEqual(vehicle["connection"], PICAR["connection"])
            for step in STEPS:
                code, message = _invoke("vehicles", "update", step, "--id", "unknown")
                self.assertEqual(code, 2)
                self.assertEqual(message.strip(), "No host 'unknown'. Hosts: piracer-test.")
            self.assertFalse((root / "unknown").exists())
