from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.physical_viability import run_physical_viability_measurement


class PhysicalViabilityTests(unittest.TestCase):
    def test_viability_pass_on_synthetic_2hz_stream(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        state = {"n": 0}

        def fake_pub(_url: str) -> dict:
            # One new processed frame per sample.
            idx = state["n"]
            state["n"] += 1
            return {
                "health": "healthy",
                "mode": "user",
                "algorithm": "lightweight_observer",
                "processed_count": idx + 1,
                "skipped_count": idx * 4,
                "min_interval_s": 0.5,
                "duration_ms": 280,
                "result_age_ms": 120,
                "control": {"steering": 0.0, "throttle": 0.0, "reason": "stable-idle-engine"},
                "frame": {"frame_id": f"donkey_frame_{idx:06d}", "has_image": True},
            }

        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp)
            # 8 samples over ~1.0s of simulated time => 8 Hz processed rate.
            mono = {"t": 0.0}

            def fake_monotonic() -> float:
                return mono["t"]

            def fake_sleep(seconds: float) -> None:
                mono["t"] += float(seconds)

            with patch(
                "cli.automa_cli.physical_viability.discover_active_vehicles",
                return_value={"active": [vehicle], "inactive": []},
            ), patch(
                "cli.automa_cli.physical_viability.find_vehicle_by_id",
                return_value=(vehicle, None),
            ), patch(
                "cli.automa_cli.physical_viability.VIABILITY_OUTPUT_ROOT",
                out_root,
            ), patch(
                "cli.automa_cli.physical_viability.time.monotonic",
                side_effect=fake_monotonic,
            ), patch(
                "cli.automa_cli.physical_viability.time.sleep",
                side_effect=fake_sleep,
            ):
                result = run_physical_viability_measurement(
                    vehicle_id="piracer",
                    duration_s=1.0,
                    sample_period_s=0.125,
                    record=True,
                    json_output=True,
                    fetch_publication=fake_pub,
                    sample_host_metrics=lambda: {
                        "pid": 1,
                        "rss_mb": 120.0,
                        "cpu_percent": 35.0,
                    },
                )
            self.assertEqual(result.exit_code, 0, result.message)
            report = json.loads(result.message)
            self.assertTrue(report["passed"])
            self.assertGreaterEqual(report["metrics"]["fresh_results_per_s"], 2.0)
            self.assertTrue((Path(report["out_dir"]) / "report.json").exists())

    def test_rejects_non_picar(self) -> None:
        vehicle = {
            "vehicle_id": "chase",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://x"},
        }
        with patch(
            "cli.automa_cli.physical_viability.discover_active_vehicles",
            return_value={"active": [vehicle], "inactive": []},
        ), patch(
            "cli.automa_cli.physical_viability.find_vehicle_by_id",
            return_value=(vehicle, None),
        ):
            result = run_physical_viability_measurement(
                vehicle_id="chase",
                duration_s=1.0,
                record=False,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("physical PiCar only", result.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
