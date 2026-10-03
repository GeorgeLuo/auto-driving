from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.physical_viability import run_memory_viability_measurement

PICAR = {
    "vehicle_id": "piracer",
    "provider": "picar",
    "connection": {"base_url": "http://piracer.local:8887"},
}


def _live(index: int, **overrides: object) -> dict:
    probe = {
        "status": "live",
        "last_health": "healthy",
        "last_epoch_id": "epoch-1",
        "last_record_count": 3,
        "last_duration_ms": 12.0,
        "last_error": None,
        "update_count": 10 + index,
        "reset_count": 0,
        "failure_count": 0,
    }
    probe.update(overrides)
    return probe


class MemoryViabilityTests(unittest.TestCase):
    def _run(self, vehicle: dict, probe=None, **kwargs):
        clock = {"t": 0.0}

        def fake_sleep(seconds: float) -> None:
            clock["t"] += float(seconds)

        with tempfile.TemporaryDirectory() as tmp, patch(
            "cli.automa_cli.physical_viability.discover_active_vehicles",
            return_value={"active": [vehicle], "inactive": []},
        ), patch(
            "cli.automa_cli.physical_viability.find_vehicle_by_id",
            return_value=(vehicle, None),
        ), patch(
            "cli.automa_cli.physical_viability.MEMORY_VIABILITY_OUTPUT_ROOT",
            Path(tmp),
        ), patch(
            "cli.automa_cli.physical_viability.time.monotonic",
            side_effect=lambda: clock["t"],
        ), patch(
            "cli.automa_cli.physical_viability.time.sleep",
            side_effect=fake_sleep,
        ):
            result = run_memory_viability_measurement(
                vehicle_id=vehicle["vehicle_id"],
                duration_s=1.0,
                sample_period_s=0.125,
                json_output=True,
                probe=probe,
                **kwargs,
            )
            if result.exit_code == 2:
                return result, {}
            report = json.loads(result.message)
            if report.get("out_dir"):
                self.assertTrue((Path(report["out_dir"]) / "report.json").exists())
            return result, report

    def _gate(self, report: dict, gate_id: str) -> bool:
        return next(g["passed"] for g in report["gates"] if g["id"] == gate_id)

    def test_pi_passes_when_updates_advance_without_failures(self) -> None:
        counter = {"n": 0}

        def probe(_vehicle: dict) -> dict:
            counter["n"] += 1
            return _live(counter["n"])

        result, report = self._run(PICAR, probe)
        self.assertEqual(result.exit_code, 0, result.message)
        self.assertTrue(report["passed"])
        self.assertEqual(report["metrics"]["epochs_seen"], ["epoch-1"])

    def test_pi_fails_each_gate_it_names(self) -> None:
        counter = {"n": 0}
        cases = {
            "memory_updates_advance": lambda i: _live(0),
            "no_memory_failures": lambda i: _live(i, failure_count=i, last_error="boom"),
            "epoch_stable": lambda i: _live(i, reset_count=i, last_epoch_id=f"epoch-{i}"),
            "health_is_known": lambda i: _live(i, last_health="degraded"),
            "p95_update_duration_at_most_100ms": lambda i: _live(i, last_duration_ms=500.0),
            "live_samples_present": lambda i: {"status": "absent", "error": "no memory step"},
        }
        for gate_id, make in cases.items():
            with self.subTest(gate=gate_id):
                counter["n"] = 0

                def probe(_vehicle: dict, make=make) -> dict:
                    counter["n"] += 1
                    return make(counter["n"])

                result, report = self._run(PICAR, probe)
                self.assertEqual(result.exit_code, 1)
                self.assertFalse(self._gate(report, gate_id))

    def test_simulator_passes_with_a_stub_and_unknown_providers_are_refused(self) -> None:
        results = {}
        for provider in ("chase-sim", "other"):
            vehicle = {"vehicle_id": "v", "provider": provider, "connection": {}}
            results[provider] = self._run(vehicle, record=False)
        self.assertEqual(results["chase-sim"][0].exit_code, 0)
        self.assertTrue(results["chase-sim"][1]["stub"])
        self.assertEqual(results["other"][0].exit_code, 2)
