from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.workbench.runner import ImageReplayRunner
from cli.automa_cli.frontend.perception_view import (
    MAX_BUFFERED_FRAMES,
    PUBLICATION_SCHEMA,
    PerceptionView,
    VIEW_RECORD_NAME,
    VIEW_SCHEMA,
    get_perception_view_status,
)


class PerceptionViewTests(unittest.TestCase):
    def test_bounded_cache_keeps_the_most_recent_frames(self) -> None:
        frame_bytes = _image_bytes("JPEG", (64, 48), (20, 40, 60))
        view = PerceptionView(host_name="test-vehicle")

        for index in range(MAX_BUFFERED_FRAMES + 1):
            record = _frame_record()
            record["report"]["frame_id"] = f"frame_{index:06d}"
            record["report"]["frame_index"] = index
            view.publish(frame_bytes=frame_bytes, frame_record=record)

        self.assertIsNone(view.frame("frame_000000"))
        self.assertIsNotNone(view.frame("frame_000001"))
        self.assertEqual(json.loads(view.latest_json())["image"]["frame_id"], f"frame_{MAX_BUFFERED_FRAMES:06d}")

    def test_view_serves_the_published_frame_and_its_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected_frame = _image_bytes("JPEG", (64, 48), (20, 40, 60))
            server = RuntimeViewServer(
                host_name="test-vehicle",
                automation_dir=root / "automation",
                port=0,
                replay_runner_factory=ImageReplayRunner,
            ).start()
            try:
                frame_record = _frame_record()
                frame_record["report"]["cycle"]["perception"]["things"] = tuple(
                    frame_record["report"]["cycle"]["perception"]["things"]
                )
                server.perception.publish(frame_bytes=expected_frame, frame_record=frame_record)

                status = get_perception_view_status(root / "automation")
                self.assertTrue(status["available"])
                self.assertEqual(status["status"], "running")
                self.assertEqual(status["schema"], VIEW_SCHEMA)
                self.assertEqual(status["latest_frame_id"], "frame_000004")

                with urlopen(f"{server.url}api/latest", timeout=1.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["schema"], PUBLICATION_SCHEMA)
                self.assertEqual(payload["image"]["frame_id"], "frame_000004")
                self.assertEqual(payload["image"]["width_px"], 64)
                self.assertEqual(payload["image"]["content_type"], "image/jpeg")
                self.assertEqual(payload["image"]["url"], "/frame?v=frame_000004")
                self.assertEqual(
                    payload["report"]["cycle"]["perception"]["things"][0]["thing_id"], "test-region"
                )
                self.assertEqual(
                    payload["report"]["cycle"]["perception"]["things"][0]["location"]["polygon_xy_norm"],
                    [[0.2, 0.25], [0.6, 0.3], [0.55, 0.75], [0.25, 0.7]],
                )

                newer_frame = _image_bytes("JPEG", (64, 48), (90, 70, 50))
                newer_record = _frame_record()
                newer_record["report"]["frame_id"] = "frame_000005"
                newer_record["report"]["frame_index"] = 5
                server.perception.publish(frame_bytes=newer_frame, frame_record=newer_record)

                versioned_url = f"{server.url.rstrip('/')}{payload['image']['url']}"
                with urlopen(versioned_url, timeout=1.0) as response:
                    self.assertEqual(response.read(), expected_frame)

                with urlopen(f"{server.url}frame", timeout=1.0) as response:
                    self.assertEqual(response.headers.get_content_type(), "image/jpeg")
                    self.assertEqual(response.read(), newer_frame)

                head_request = Request(f"{server.url}frame", method="HEAD")
                with urlopen(head_request, timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "image/jpeg")
                    self.assertEqual(int(response.headers["Content-Length"]), len(newer_frame))
                    self.assertEqual(response.read(), b"")

                with urlopen(f"{server.url}perception", timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "text/html")

                with urlopen(f"{server.url}", timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                with urlopen(f"{server.url}decision", timeout=1.0) as response:
                    self.assertEqual(response.status, 200)

                with urlopen(f"{server.url}favicon.ico", timeout=1.0) as response:
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.read(), b"")
            finally:
                server.stop()

    def test_invalid_image_is_refused_and_a_later_image_recovers(self) -> None:
        view = PerceptionView(host_name="test-vehicle")
        good = _image_bytes("JPEG", (4, 4), (9, 9, 9))
        record = _frame_record()
        view.publish(frame_bytes=good, frame_record=record)
        for bad in (b"", b"not-an-image", _image_bytes("PNG", (4, 4), (9, 9, 9))):
            with self.subTest(bad=bad[:8]):
                view.publish(frame_bytes=bad, frame_record=record)
                self.assertEqual(json.loads(view.latest_json())["report"], record["report"])
                self.assertIsNone(json.loads(view.latest_json())["image"]["url"])
            self.assertEqual(view.frame()[0], good)
        replacement = _image_bytes("JPEG", (5, 6), (3, 3, 3))
        next_record = _frame_record()
        next_record["report"]["frame_id"] = "frame_000008"
        next_record["report"]["frame_index"] = 8
        view.publish(frame_bytes=replacement, frame_record=next_record)
        self.assertEqual(view.frame(), (replacement, "image/jpeg"))
        payload = json.loads(view.latest_json())
        self.assertEqual((payload["image"]["width_px"], payload["image"]["height_px"]), (5, 6))

    def test_result_age_is_measured_from_the_published_findings(self) -> None:
        view = PerceptionView(host_name="test-vehicle")
        view.publish(frame_bytes=_image_bytes("JPEG", (4, 4), (9, 9, 9)), frame_record=_frame_record())

        with patch("cli.automa_cli.frontend.perception_view._timestamp_ms", return_value=1300):
            payload = json.loads(view.latest_json())

        self.assertEqual((payload["read_at_ms"], payload["result_age_ms"]), (1300, 1300 - 1242))

    def test_view_rejects_data_requests_before_first_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = RuntimeViewServer(
                host_name="test-vehicle",
                automation_dir=Path(tmp),
                port=0,
                replay_runner_factory=ImageReplayRunner,
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

            server = RuntimeViewServer(
                host_name="test-vehicle",
                automation_dir=root,
                port=0,
                replay_runner_factory=ImageReplayRunner,
            ).start()
            server.stop()

            status = get_perception_view_status(root)
            self.assertFalse(status["available"])
            self.assertEqual(status["status"], "unavailable")

    def test_a_second_view_of_the_host_leaves_the_first_one_recorded(self) -> None:
        """A stream that starts and stops beside ``hosts viewer`` does not hide the viewer."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = (
                RuntimeViewServer(host_name="test-vehicle", automation_dir=root, port=0,
                                  replay_runner_factory=ImageReplayRunner)
                for _ in range(2)
            )
            first_url = first.start().url
            try:
                second.start()
                second.stop()
                status = get_perception_view_status(root)
            finally:
                first.stop()
        self.assertEqual((status["url"], status["status"]), (first_url, "warming"))

    def test_a_recorded_port_that_serves_another_host_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = RuntimeViewServer(host_name="other-vehicle", automation_dir=None, port=0,
                                      replay_runner_factory=ImageReplayRunner).start()
            try:
                (root / VIEW_RECORD_NAME).write_text(json.dumps({"host_name": "test-vehicle", "url": other.url}))
                status = get_perception_view_status(root)
            finally:
                other.stop()
        self.assertEqual((status["available"], status["status"]), (False, "unavailable"))
        self.assertIn("other-vehicle", status["reason"])


def _image_bytes(fmt: str, size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _frame_record() -> dict:
    perception = {
        "schema": "perception_text",
        "status": "ok",
        "confidence": 0.8,
        "plugin_runs": [
            {
                "plugin_id": "test-plugin",
                "status": "ok",
                "duration_ms": 8,
                "thing_count": 2,
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
    }
    return {
        "schema": "host_frame_publication",
        "health": "healthy",
        "ok": True,
        "host_name": "test-vehicle",
        "stale_after_ms": 1_000,
        "report": {
            "host_name": "test-vehicle",
            "frame_id": "frame_000004",
            "frame_index": 4,
            "timestamp_ms": 1234,
            "published_at_ms": 1242,
            "status": "ok",
            "error": None,
            "cycle": {"perception": perception, "memory": None},
            "values": {
                "timing": {
                    "duration_ms": 17,
                    "steps": [{"step": "perception", "duration_ms": 8, "plugins": []}],
                },
            },
        },
    }


if __name__ == "__main__":
    unittest.main(verbosity=2)
