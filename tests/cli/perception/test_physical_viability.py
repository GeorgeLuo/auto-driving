from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.viability import run_perception_viability_measurement


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
                "preset": "lightweight_observer",
                "processed_count": idx + 1,
                "skipped_count": idx * 4,
                "interval_s": 0.5,
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
                "cli.automa_cli.viability.discover_active_vehicles",
                return_value={"active": [vehicle], "inactive": []},
            ), patch(
                "cli.automa_cli.viability.find_vehicle_by_id",
                return_value=(vehicle, None),
            ), patch(
                "cli.automa_cli.viability.PERCEPTION_VIABILITY_OUTPUT_ROOT",
                out_root,
            ), patch(
                "cli.automa_cli.viability.time.monotonic",
                side_effect=fake_monotonic,
            ), patch(
                "cli.automa_cli.viability.time.sleep",
                side_effect=fake_sleep,
            ):
                result = run_perception_viability_measurement(
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

    def test_simulator_is_measured_and_unknown_providers_are_refused(self) -> None:
        vehicle = {"vehicle_id": "v", "provider": "chase-sim", "connection": {}}
        state = {"n": 0}

        def fake_pub(_url: str) -> dict:
            idx = state["n"]
            state["n"] += 1
            return {
                "health": "healthy",
                "mode": "observe_only",
                "processed_count": idx + 1,
                "skipped_count": 0,
                "interval_s": 0.5,
                "duration_ms": 280,
                "result_age_ms": 120,
                "control": {"steering": 0.0, "throttle": 0.0},
                "frame": {"frame_id": f"frame_{idx:06d}"},
            }

        mono = {"t": 0.0}
        with patch(
            "cli.automa_cli.viability.discover_active_vehicles",
            return_value={"active": [vehicle], "inactive": []},
        ), patch(
            "cli.automa_cli.viability.find_vehicle_by_id",
            return_value=(vehicle, None),
        ), patch(
            "cli.automa_cli.viability.time.monotonic",
            side_effect=lambda: mono["t"],
        ), patch(
            "cli.automa_cli.viability.time.sleep",
            side_effect=lambda seconds: mono.__setitem__("t", mono["t"] + float(seconds)),
        ):
            measured = run_perception_viability_measurement(
                vehicle_id="v",
                duration_s=1.0,
                sample_period_s=0.125,
                record=False,
                json_output=True,
                fetch_publication=fake_pub,
            )
        self.assertEqual(measured.exit_code, 0, measured.message)
        report = json.loads(measured.message)
        self.assertTrue(report["passed"])
        self.assertNotIn("stub", report)

        other = {"vehicle_id": "v", "provider": "other", "connection": {}}
        with patch(
            "cli.automa_cli.viability.discover_active_vehicles",
            return_value={"active": [other], "inactive": []},
        ), patch(
            "cli.automa_cli.viability.find_vehicle_by_id",
            return_value=(other, None),
        ):
            refused = run_perception_viability_measurement(
                vehicle_id="v", duration_s=1.0, record=False, json_output=True
            )
        self.assertEqual(refused.exit_code, 2)
        self.assertIn("picar and chase-sim", refused.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
