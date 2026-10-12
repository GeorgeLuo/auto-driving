from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

from autonomy.decision_cycle.activation import STEPS
from cli.automa_cli.app import main
from cli.automa_cli.step_activations import (
    bundle_activation_path,
    read_bundle_activation,
    staging_vehicle,
    vehicle_bundle,
)

DISCOVERY = "cli.automa_cli.vehicles.discover_active_vehicles"
PICAR = {
    "vehicle_id": "piracer-test",
    "vehicle_kind": "picar",
    "provider": "picar",
    "connection": {"base_url": "http://piracer-test.local:8887", "source": "test"},
}


def _invoke(*args: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(args))
    return code, stdout.getvalue()


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
