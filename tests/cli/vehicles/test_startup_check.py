from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.operations import run_vehicle_startup_check
from implementations.vehicle.access import VehicleAccess
from tests.implementations.operations.test_startup_action_check import FakeImageCar


class _Control:
    def __init__(self) -> None:
        self.events: list[str] = []

    def acquire(self) -> dict[str, str]:
        self.events.append("acquire")
        return {"mode": "autonomy"}

    def release(self) -> None:
        self.events.append("release")


class StartupCheckTests(unittest.TestCase):
    def _check(self, *, dry_run: bool) -> tuple[int, str, dict, FakeImageCar, _Control]:
        car, control = FakeImageCar(), _Control()
        access = VehicleAccess(car=car, image_extension="jpg", front_camera_endpoint="", control=control)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("cli.automa_cli.operations.OPERATION_OUTPUT_ROOT", Path(tmp)),
            patch("cli.automa_cli.operations.discover_vehicle", return_value=({"vehicle_id": "fake-car"}, None)),
            patch("cli.automa_cli.operations.create_vehicle_access", return_value=access),
        ):
            result = run_vehicle_startup_check(
                vehicle_id="fake-car", duration_s=0.0, settle_s=0.0, dry_run=dry_run
            )
            report = json.loads(next(Path(tmp).glob("*/report.json")).read_text())
        return result.exit_code, result.message, report, car, control

    def test_a_dry_run_captures_without_control_and_exits_zero(self) -> None:
        code, message, report, car, control = self._check(dry_run=True)

        self.assertEqual(code, 0, message)
        self.assertIn("Result: dry run, no control acquired; 7 frame pairs captured, not scored", message)
        self.assertEqual((car.pulses, control.events, report["control"]), ([], [], []))
        self.assertIsNone(report["passed"])

    def test_a_live_check_records_acquire_and_release_in_its_report(self) -> None:
        code, message, report, car, control = self._check(dry_run=False)

        self.assertEqual(code, 0, message)
        self.assertIn("Result: passed (7/7 checks)", message)
        self.assertEqual(len(car.pulses), 7)
        self.assertEqual(control.events, ["acquire", "release"])
        self.assertEqual([event["event"] for event in report["control"]], ["acquire", "release"])
        self.assertEqual(report["control"][0]["receipt"], {"mode": "autonomy"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
