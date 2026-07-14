from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from cli.automa_cli.perception_view import (
    PUBLICATION_SCHEMA,
    VIEW_SCHEMA,
    PerceptionViewServer,
    get_perception_view_status,
)


class PerceptionViewTests(unittest.TestCase):
    def test_view_serves_live_frame_with_independently_updated_perception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame_path = root / "frame.png"
            Image.new("RGB", (64, 48), (20, 40, 60)).save(frame_path)
            expected_frame = frame_path.read_bytes()
            server = PerceptionViewServer(
                vehicle_id="test-vehicle",
                automation_dir=root / "automation",
                port=0,
            ).start()
            try:
                frame_record = _frame_record()
                frame_record["perception"]["things"] = tuple(
                    frame_record["perception"]["things"]
                )
                server.publish_frame(
                    frame_path=frame_path,
                    frame_record=frame_record,
                )

                status = get_perception_view_status(root / "automation")
                self.assertTrue(status["available"])
                self.assertEqual(status["schema"], VIEW_SCHEMA)
                self.assertEqual(status["latest_frame_id"], "frame_000004")

                with urlopen(f"{server.url}api/latest", timeout=1.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["schema"], PUBLICATION_SCHEMA)
                self.assertEqual(payload["frame"]["frame_id"], "frame_000004")
                self.assertEqual(payload["frame"]["width_px"], 64)
                self.assertEqual(payload["frame"]["url"], "/frame?v=frame_000004")
                self.assertEqual(payload["overlay"]["status"], "pending")
                self.assertIsNone(payload["perception"])

                server.publish_perception(frame_record=frame_record)
                with urlopen(f"{server.url}api/latest", timeout=1.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["overlay"]["status"], "current")
                self.assertEqual(payload["overlay"]["source_frame_id"], "frame_000004")
                self.assertEqual(payload["overlay"]["frame_lag"], 0)
                self.assertEqual(payload["perception"]["things"][0]["thing_id"], "test-region")
                self.assertEqual(
                    payload["perception"]["things"][0]["location"]["polygon_xy_norm"],
                    [[0.2, 0.25], [0.6, 0.3], [0.55, 0.75], [0.25, 0.7]],
                )

                newer_frame_path = root / "newer.png"
                Image.new("RGB", (64, 48), (90, 70, 50)).save(newer_frame_path)
                newer_record = _frame_record()
                newer_record["frame_id"] = "frame_000005"
                newer_record["frame_index"] = 5
                newer_record["captured_at_ms"] = 1534
                server.publish_frame(frame_path=newer_frame_path, frame_record=newer_record)

                with urlopen(f"{server.url}api/latest", timeout=1.0) as response:
                    stale_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(stale_payload["frame"]["frame_id"], "frame_000005")
                self.assertEqual(stale_payload["overlay"]["status"], "stale")
                self.assertEqual(stale_payload["overlay"]["source_frame_id"], "frame_000004")
                self.assertEqual(stale_payload["overlay"]["frame_lag"], 1)
                self.assertEqual(stale_payload["overlay"]["frame_lag_ms"], 300)

                versioned_url = f"{server.url.rstrip('/')}{payload['frame']['url']}"
                with urlopen(versioned_url, timeout=1.0) as response:
                    self.assertEqual(response.read(), expected_frame)

                with urlopen(f"{server.url}frame", timeout=1.0) as response:
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertEqual(response.read(), newer_frame_path.read_bytes())

                head_request = Request(f"{server.url}frame", method="HEAD")
                with urlopen(head_request, timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertEqual(
                        int(response.headers["Content-Length"]),
                        len(newer_frame_path.read_bytes()),
                    )
                    self.assertEqual(response.read(), b"")

                with urlopen(server.url, timeout=1.0) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("Automa Perception", html)
                self.assertIn('id="regionsToggle"', html)
                self.assertIn('id="labelsToggle"', html)
                self.assertIn('id="kindToggles"', html)
                self.assertIn("Overlay lag", html)

                with urlopen(f"{server.url}favicon.ico", timeout=1.0) as response:
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.read(), b"")
            finally:
                server.stop()

    def test_view_rejects_data_requests_before_first_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = PerceptionViewServer(
                vehicle_id="test-vehicle",
                automation_dir=Path(tmp),
                port=0,
            ).start()
            try:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(f"{server.url}api/latest", timeout=1.0)

                self.assertEqual(caught.exception.code, 503)
            finally:
                server.stop()

    def test_view_status_is_unavailable_without_a_running_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = get_perception_view_status(root)
            self.assertFalse(status["available"])

            server = PerceptionViewServer(
                vehicle_id="test-vehicle",
                automation_dir=root,
                port=0,
            ).start()
            server.stop()

            status = get_perception_view_status(root)
            self.assertFalse(status["available"])
            self.assertEqual(status["status"], "unavailable")


def _frame_record() -> dict:
    return {
        "frame_id": "frame_000004",
        "frame_index": 4,
        "captured_at_ms": 1234,
        "cycle_duration_ms": 17,
        "perception_duration_ms": 8,
        "control_source": "simulator",
        "control_application": "not_applied",
        "action_policy": "observe_only",
        "sensor_snapshot": {
            "readings": {
                "front_camera": {
                    "metadata": {"content_type": "image/png"},
                }
            }
        },
        "perception": {
            "schema": "perception_text_v2",
            "status": "ok",
            "confidence": 0.8,
            "plugin_runs": [
                {
                    "plugin_id": "test-plugin",
                    "status": "ok",
                    "duration_ms": 8,
                    "thing_count": 2,
                    "artifact_count": 0,
                }
            ],
            "things": [
                {
                    "thing_id": "test-region",
                    "kind": "region",
                    "label": "test region",
                    "confidence": 0.8,
                    "location": {
                        "frame": "image",
                        "zone": "near_center",
                        "bbox_xyxy_norm": [0.2, 0.25, 0.6, 0.75],
                        "polygon_xy_norm": [
                            [0.2, 0.25],
                            [0.6, 0.3],
                            [0.55, 0.75],
                            [0.25, 0.7],
                        ],
                    },
                    "properties": {},
                },
                {
                    "thing_id": "front_camera_frame",
                    "kind": "sensor_frame",
                    "label": "front camera frame",
                    "confidence": 1.0,
                    "location": {
                        "frame": "image",
                        "zone": "full_frame",
                        "bbox_xyxy_norm": [0.0, 0.0, 1.0, 1.0],
                    },
                    "properties": {"width_px": 64, "height_px": 48},
                },
            ],
        },
        "observation": None,
        "control": {"steering": 0.0, "throttle": 0.0},
        "engine": {"engine": "idle"},
    }


if __name__ == "__main__":
    unittest.main(verbosity=2)
