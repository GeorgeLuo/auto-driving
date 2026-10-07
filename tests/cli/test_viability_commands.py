from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli import viability
from tests.cli.perception.test_physical_stream import _publication
from tests.support.cli_runner import run_automa


class ViabilityCommandTests(unittest.TestCase):
    def test_help_describes_both_providers_and_recording_in_json_mode(self) -> None:
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = run_automa("vehicles", step, "viability", "--help")
                text = " ".join(result.stdout.split())
                self.assertIn("Chase returns a stub pass", text)
                self.assertIn("PiCar measurement or Chase stub", text)
                self.assertIn("PiCar reports are also saved unless --no-record", text)
                self.assertNotIn("picar only", text)

    def test_public_json_discovery_failures_are_parseable_and_record_nothing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for step in ("perception", "memory"):
                with self.subTest(step=step):
                    result = run_automa(
                        "vehicles",
                        step,
                        "viability",
                        "--id",
                        "unknown-viability",
                        "--json",
                        check=False,
                        extra_env={
                            f"AUTOMA_{step.upper()}_VIABILITY_OUTPUT_ROOT": str(
                                root / step
                            )
                        },
                    )
                    self.assertEqual(result.returncode, 2)
                    error = json.loads(result.stdout)
                    self.assertEqual(error["schema"], "vehicle_step_viability_error_v0")
                    self.assertEqual(error["step"], step)
                    self.assertEqual(error["vehicle_id"], "unknown-viability")
                    self.assertEqual(error["error"], "unknown_vehicle")
                    self.assertIn("was not found", error["message"])
                    self.assertEqual(result.stderr, "")
            self.assertEqual(list(root.iterdir()), [])

    def test_preflight_failures_share_exit_and_diagnostics_without_measuring(
        self,
    ) -> None:
        cases = (
            ([], "unknown_vehicle"),
            ([{"vehicle_id": "v", "provider": "other"}], "unsupported_provider"),
            (
                [{"vehicle_id": "v", "provider": "picar", "connection": {}}],
                "missing_connection",
            ),
        )
        for step in ("perception", "memory"):
            run = getattr(viability, f"run_{step}_viability_measurement")
            for vehicles, error_code in cases:
                with (
                    self.subTest(step=step, error=error_code),
                    tempfile.TemporaryDirectory() as temporary,
                    patch.object(
                        viability,
                        "discover_active_vehicles",
                        return_value={"vehicles": vehicles},
                    ),
                    patch.object(
                        viability,
                        f"{step.upper()}_VIABILITY_OUTPUT_ROOT",
                        Path(temporary),
                    ),
                    patch.object(
                        viability.time,
                        "sleep",
                        side_effect=AssertionError("preflight must not sample"),
                    ),
                ):
                    machine = run(vehicle_id="v", json_output=True)
                    human = run(vehicle_id="v")
                    self.assertEqual(machine.exit_code, 2)
                    self.assertEqual(human.exit_code, 2)
                    error = json.loads(machine.message)
                    self.assertEqual(error["error"], error_code)
                    self.assertEqual(error["message"], human.message)
                    self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_chase_stub_returns_without_sampling_or_recording(self) -> None:
        vehicle = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"}
        for step in ("perception", "memory"):
            with (
                self.subTest(step=step),
                tempfile.TemporaryDirectory() as temporary,
                patch.object(
                    viability,
                    "discover_active_vehicles",
                    return_value={"vehicles": [vehicle]},
                ),
                patch.object(
                    viability, f"{step.upper()}_VIABILITY_OUTPUT_ROOT", Path(temporary)
                ),
            ):
                run = getattr(viability, f"run_{step}_viability_measurement")
                kwargs = {
                    "fetch_publication"
                    if step == "perception"
                    else "probe": lambda _: self.fail("stub must not sample")
                }
                result = run(
                    vehicle_id=vehicle["vehicle_id"], json_output=True, **kwargs
                )
                report = json.loads(result.message)
                self.assertEqual(result.exit_code, 0)
                self.assertTrue(report["passed"])
                self.assertTrue(report["stub"])
                self.assertEqual(report["provider"], "chase-sim")
                self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_picar_json_reports_obey_the_record_option(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://fixture.local"},
        }
        for step in ("perception", "memory"):
            for record in (True, False):
                clock = {"t": 0.0}

                def sleep(seconds: float, *, clock=clock) -> None:
                    clock["t"] += seconds

                with (
                    self.subTest(step=step, record=record),
                    tempfile.TemporaryDirectory() as temporary,
                    patch.object(
                        viability,
                        "discover_active_vehicles",
                        return_value={"vehicles": [vehicle]},
                    ),
                    patch.object(
                        viability,
                        f"{step.upper()}_VIABILITY_OUTPUT_ROOT",
                        Path(temporary),
                    ),
                    patch.object(
                        viability.time,
                        "monotonic",
                        side_effect=lambda clock=clock: clock["t"],
                    ),
                    patch.object(viability.time, "sleep", side_effect=sleep),
                ):
                    run = getattr(viability, f"run_{step}_viability_measurement")
                    kwargs = (
                        {"fetch_publication": lambda _: _publication()}
                        if step == "perception"
                        else {
                            "probe": lambda _: {
                                "status": "absent",
                                "error": "fixture absent",
                            }
                        }
                    )
                    result = run(
                        vehicle_id=vehicle["vehicle_id"],
                        duration_s=1,
                        sample_period_s=0.25,
                        json_output=True,
                        record=record,
                        **kwargs,
                    )
                    report = json.loads(result.message)
                    self.assertIn(result.exit_code, (0, 1))
                    self.assertEqual(
                        report["schema"],
                        {
                            "perception": "automa_physical_perception_viability_v0",
                            "memory": "automa_physical_memory_viability_v1",
                        }[step],
                    )
                    if record:
                        saved = json.loads(Path(report["report_json"]).read_text())
                        self.assertEqual(saved["metrics"], report["metrics"])
                        if step == "perception":
                            self.assertTrue(Path(report["summary_md"]).is_file())
                    else:
                        self.assertNotIn("out_dir", report)
                        self.assertEqual(list(Path(temporary).iterdir()), [])
