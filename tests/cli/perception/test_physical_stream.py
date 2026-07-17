from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli import streaming
from cli.automa_cli.physical_observation import (
    perception_text_from_publication,
    publication_to_frame_record,
)


def _publication(**overrides):
    payload = {
        "schema": "automa_physical_observation_publication_v0",
        "ok": True,
        "health": "healthy",
        "result_age_ms": 120,
        "duration_ms": 280,
        "min_interval_s": 0.5,
        "processed_count": 12,
        "skipped_count": 40,
        "algorithm": "lightweight_observer",
        "mode": "user",
        "drive_mode": "user",
        "control": {
            "steering": 0.0,
            "throttle": 0.0,
            "reason": "stable-idle-engine",
            "confidence": 1.0,
            "metadata": {},
        },
        "frame": {
            "frame_id": "donkey_frame_000011",
            "frame_index": 11,
            "captured_at_ms": 1000,
            "completed_at_ms": 1280,
            "has_image": True,
            "frame_path": "/autonomy/observation/latest/frame.jpg",
        },
        "perception": {
            "schema": "perception_text_v2",
            "status": "ok",
            "text": "floor visible\nboundary center",
            "lines": ["floor visible", "boundary center"],
            "signals": [{"signal_id": "floor_visible"}],
            "things": [{"thing_id": "boundary-1"}],
        },
        "observation": {"schema": "decision_observation_v0"},
    }
    payload.update(overrides)
    return payload


class PhysicalObservationAdapterTests(unittest.TestCase):
    def test_publication_to_frame_record_pairs_findings(self) -> None:
        record = publication_to_frame_record(_publication())
        self.assertEqual(record["frame_id"], "donkey_frame_000011")
        self.assertEqual(record["perception"]["things"][0]["thing_id"], "boundary-1")
        self.assertEqual(record["control"]["steering"], 0.0)
        self.assertEqual(record["control_source"], "physical_onboard")
        self.assertEqual(record["action_policy"], "observe_only")

    def test_perception_text_prefers_lines(self) -> None:
        text = perception_text_from_publication(_publication())
        self.assertIn("floor visible", text)
        self.assertIn("boundary center", text)


class PhysicalStreamCommandTests(unittest.TestCase):
    def test_stream_once_renders_physical_onboard_snapshot(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        discovery = {
            "active": [vehicle],
            "inactive": [],
        }
        jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xd9"
        )
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp)
            with patch.object(streaming, "discover_active_vehicles", return_value=discovery), patch.object(
                streaming, "find_vehicle_by_id", return_value=(vehicle, None)
            ), patch.object(
                streaming,
                "fetch_observation_publication",
                return_value=_publication(),
            ), patch.object(
                streaming,
                "fetch_observation_frame",
                return_value=(jpeg, {"content-type": "image/jpeg", "x-frame-id": "donkey_frame_000011"}),
            ), patch(
                "cli.automa_cli.streaming.physical_observation_dir",
                return_value=runtime_root / "piracer" / "physical_observation",
            ):
                buffer = io.StringIO()
                result = streaming.stream_vehicle_perception(
                    vehicle_id="piracer",
                    once=True,
                    no_clear=True,
                    output=buffer,
                )
            self.assertEqual(result.exit_code, 0)
            text = buffer.getvalue()
            self.assertIn("source: physical onboard", text)
            self.assertIn("status: healthy", text)
            self.assertIn("donkey_frame_000011", text)
            self.assertIn("steering=0.0", text)
            self.assertIn("floor visible", text)
            self.assertIn("view: http://127.0.0.1:", text)

    def test_stream_once_reports_connection_failure(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        discovery = {"active": [vehicle], "inactive": []}
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp)
            with patch.object(streaming, "discover_active_vehicles", return_value=discovery), patch.object(
                streaming, "find_vehicle_by_id", return_value=(vehicle, None)
            ), patch.object(
                streaming,
                "fetch_observation_publication",
                side_effect=ConnectionError("GET failed: connection refused"),
            ), patch(
                "cli.automa_cli.streaming.physical_observation_dir",
                return_value=runtime_root / "piracer" / "physical_observation",
            ):
                buffer = io.StringIO()
                result = streaming.stream_vehicle_perception(
                    vehicle_id="piracer",
                    once=True,
                    no_clear=True,
                    output=buffer,
                )
            self.assertEqual(result.exit_code, 2)
            self.assertIn("connection refused", result.message)
            self.assertIn("status: unavailable", buffer.getvalue())

    def test_chase_stream_still_requires_automation_runtime(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://localhost:5050/ws/control"},
        }
        discovery = {"active": [vehicle], "inactive": []}
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with patch.object(streaming, "discover_active_vehicles", return_value=discovery), patch.object(
                streaming, "find_vehicle_by_id", return_value=(vehicle, None)
            ), patch.object(streaming, "_automation_dir", return_value=missing):
                result = streaming.stream_vehicle_perception(
                    vehicle_id="chase-sim",
                    once=True,
                    output=io.StringIO(),
                )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("No automation runtime exists", result.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
