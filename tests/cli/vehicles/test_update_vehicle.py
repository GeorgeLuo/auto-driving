from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

from cli.automa_cli.app import main
from cli.automa_cli.step_activations import GENERIC_UPDATE_STEPS


UPDATE_STEPS = ("perception", "memory", *GENERIC_UPDATE_STEPS)
DISCOVERY = "cli.automa_cli.step_activations.discover_active_vehicles"
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
                patch("cli.automa_cli.perception.RUNTIME_ROOT", root),
                patch("cli.automa_cli.memory.RUNTIME_ROOT", root),
                patch("cli.automa_cli.app.DECISION_RUNTIME_ROOT", root),
            ):
                yield root

    def test_every_step_rejects_an_id_discovery_does_not_find(self) -> None:
        for step in UPDATE_STEPS:
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
                for step in UPDATE_STEPS:
                    with self.subTest(vehicle_id=vehicle_id, step=step), patch(DISCOVERY, offline):
                        code, stdout = _invoke(
                            "vehicles", "update", step, "--id", vehicle_id, "--dry-run"
                        )
                        self.assertEqual(code, 0, stdout)
            offline.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
