from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.physical_check import run_physical_perception_check, score_placement


def _publication(
    *,
    health: str = "healthy",
    frame_id: str = "donkey_frame_000001",
    zones: list[str] | None = None,
    floor_visible: bool = True,
    steering: float = 0.0,
    throttle: float = 0.0,
    mode: str = "user",
) -> dict:
    things = []
    for index, zone in enumerate(zones or []):
        things.append(
            {
                "thing_id": f"floor_boundary_{index:03d}",
                "kind": "floor_boundary",
                "zone": zone,
                "confidence": 0.8,
            }
        )
    if floor_visible:
        things.append(
            {
                "thing_id": "traversable_floor",
                "kind": "surface",
                "zone": "visible_floor",
                "confidence": 1.0,
            }
        )
    return {
        "health": health,
        "ok": health in {"healthy", "stale"},
        "mode": mode,
        "algorithm": "lightweight_observer",
        "duration_ms": 280,
        "result_age_ms": 100,
        "control": {
            "steering": steering,
            "throttle": throttle,
            "reason": "stable-idle-engine",
            "confidence": 1.0,
            "metadata": {},
        },
        "frame": {
            "frame_id": frame_id,
            "frame_index": 1,
            "captured_at_ms": 1000,
            "completed_at_ms": 1280,
            "has_image": health == "healthy",
            "frame_path": "/autonomy/observation/latest/frame.jpg",
        },
        "perception": {
            "status": "ok" if health == "healthy" else health,
            "signals": [
                {"signal_id": "floor_visible", "value": floor_visible},
            ],
            "things": things,
        },
    }


class PhysicalCheckScoringTests(unittest.TestCase):
    def test_clear_left_center_right_removed_and_unavailable(self) -> None:
        clear = score_placement(placement="clear", publication=_publication(zones=[]))
        self.assertTrue(clear["passed"])

        left = score_placement(
            placement="left",
            publication=_publication(zones=["mid_left"], frame_id="f2"),
        )
        self.assertTrue(left["passed"])
        self.assertIn("left", left["zones"])

        center = score_placement(
            placement="center",
            publication=_publication(zones=["center"], frame_id="f3"),
        )
        self.assertTrue(center["passed"])

        right = score_placement(
            placement="right",
            publication=_publication(zones=["mid_right"], frame_id="f4"),
        )
        self.assertTrue(right["passed"])

        removed = score_placement(
            placement="removed",
            publication=_publication(zones=[], frame_id="f5"),
            previous_publication=_publication(zones=["mid_right"], frame_id="f4"),
        )
        self.assertTrue(removed["passed"])

        unavailable = score_placement(
            placement="unavailable",
            publication=_publication(health="unavailable", zones=[], frame_id="f6"),
        )
        self.assertTrue(unavailable["passed"])

    def test_nonzero_control_fails_safety_check(self) -> None:
        score = score_placement(
            placement="clear",
            publication=_publication(steering=0.2, throttle=0.0, zones=[]),
        )
        self.assertFalse(score["passed"])
        self.assertIn("control_zero", score["failed_checks"])

    def test_missing_target_zone_fails(self) -> None:
        score = score_placement(
            placement="left",
            publication=_publication(zones=["mid_right"], frame_id="f9"),
        )
        self.assertFalse(score["passed"])
        self.assertIn("boundary_left", score["failed_checks"])


class PhysicalCheckCommandTests(unittest.TestCase):
    def test_auto_recorded_check_writes_review(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        discovery = {"active": [vehicle], "inactive": []}
        sequence = [
            _publication(frame_id="f1", zones=[]),
            _publication(frame_id="f2", zones=["mid_left"]),
            _publication(frame_id="f3", zones=["center"]),
            _publication(frame_id="f4", zones=["mid_right"]),
            _publication(frame_id="f5", zones=[]),
            _publication(health="unavailable", frame_id="f6", zones=[]),
        ]
        calls = {"n": 0}

        def fake_pub(_url: str) -> dict:
            payload = sequence[min(calls["n"], len(sequence) - 1)]
            calls["n"] += 1
            return payload

        def fake_frame(_url: str) -> tuple[bytes, dict[str, str]]:
            return b"\xff\xd8\xff\xd9", {"x-frame-id": "test"}

        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp)
            with patch(
                "cli.automa_cli.physical_check.discover_active_vehicles",
                return_value=discovery,
            ), patch(
                "cli.automa_cli.physical_check.find_vehicle_by_id",
                return_value=(vehicle, None),
            ), patch(
                "cli.automa_cli.physical_check.CHECK_OUTPUT_ROOT",
                out_root,
            ):
                result = run_physical_perception_check(
                    vehicle_id="piracer",
                    record=True,
                    auto=True,
                    steps=("clear", "left", "center", "right", "removed", "unavailable"),
                    json_output=True,
                    fetch_publication=fake_pub,
                    fetch_frame=fake_frame,
                    fresh_timeout_s=1.0,
                )
            self.assertEqual(result.exit_code, 0)
            report = json.loads(result.message)
            self.assertTrue(report["passed"])
            self.assertEqual(len(report["step_results"]), 6)
            out_dir = out_root / report["run_id"]
            self.assertTrue((out_dir / "report.json").exists())
            self.assertTrue((out_dir / "review.html").exists())
            self.assertTrue((out_dir / "02-left" / "frame.jpg").exists())
            self.assertFalse(report["safety"]["movement_commands_sent"])

    def test_rejects_non_picar_vehicle(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://localhost:5050/ws/control"},
        }
        with patch(
            "cli.automa_cli.physical_check.discover_active_vehicles",
            return_value={"active": [vehicle], "inactive": []},
        ), patch(
            "cli.automa_cli.physical_check.find_vehicle_by_id",
            return_value=(vehicle, None),
        ):
            result = run_physical_perception_check(
                vehicle_id="chase-sim",
                auto=True,
                steps=("clear",),
            )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("only supported for physical PiCar", result.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
