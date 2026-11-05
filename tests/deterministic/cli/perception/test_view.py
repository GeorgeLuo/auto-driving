from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
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
    VIEW_SCHEMA,
    get_perception_view_status,
)


class PerceptionViewTests(unittest.TestCase):
    def test_retained_in_flight_frame_survives_bounded_cache_turnover(self) -> None:
        frame_bytes = _image_bytes("PNG", (64, 48), (20, 40, 60))
        view = PerceptionView(host_name="test-vehicle")

        for index in range(MAX_BUFFERED_FRAMES):
            record = _frame_record()
            record["frame"]["frame_id"] = f"frame_{index:06d}"
            record["frame"]["frame_index"] = index
            view.publish_frame(
                frame_bytes=frame_bytes, content_type="image/png", frame_record=record,
            )
            if index == 0:
                self.assertTrue(view.retain_frame(record["frame"]["frame_id"]))

        overflow = _frame_record()
        overflow["frame"]["frame_id"] = f"frame_{MAX_BUFFERED_FRAMES:06d}"
        overflow["frame"]["frame_index"] = MAX_BUFFERED_FRAMES
        view.publish_frame(
            frame_bytes=frame_bytes, content_type="image/png", frame_record=overflow,
        )
        self.assertIsNotNone(view.frame("frame_000000"))
        self.assertIsNone(view.frame("frame_000001"))

        view.release_frame("frame_000000")
        next_frame = _frame_record()
        next_frame["frame"]["frame_id"] = f"frame_{MAX_BUFFERED_FRAMES + 1:06d}"
        next_frame["frame"]["frame_index"] = MAX_BUFFERED_FRAMES + 1
        view.publish_frame(
            frame_bytes=frame_bytes, content_type="image/png", frame_record=next_frame,
        )
        self.assertIsNone(view.frame("frame_000000"))

    def test_view_serves_live_frame_with_independently_updated_perception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected_frame = _image_bytes("PNG", (64, 48), (20, 40, 60))
            server = RuntimeViewServer(
                host_name="test-vehicle",
                automation_dir=root / "automation",
                port=0,
                replay_runner_factory=ImageReplayRunner,
            ).start()
            try:
                frame_record = _frame_record()
                frame_record["perception"]["things"] = tuple(
                    frame_record["perception"]["things"]
                )
                server.perception.publish_frame(
                    frame_bytes=expected_frame,
                    content_type="image/png",
                    frame_record=frame_record,
                )

                status = get_perception_view_status(root / "automation")
                self.assertFalse(status["available"])
                self.assertEqual(status["status"], "warming")
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

                server.perception.publish_perception(frame_record=frame_record)
                status = get_perception_view_status(root / "automation")
                self.assertTrue(status["available"])
                self.assertEqual(status["status"], "running")
                with urlopen(f"{server.url}api/latest", timeout=1.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["overlay"]["status"], "current")
                self.assertEqual(payload["overlay"]["source_frame_id"], "frame_000004")
                self.assertEqual(payload["overlay"]["frame_lag"], 0)
                self.assertEqual(
                    payload["perception"]["things"][0]["thing_id"], "test-region"
                )
                self.assertEqual(
                    payload["perception"]["things"][0]["location"]["polygon_xy_norm"],
                    [[0.2, 0.25], [0.6, 0.3], [0.55, 0.75], [0.25, 0.7]],
                )

                newer_frame = _image_bytes("PNG", (64, 48), (90, 70, 50))
                newer_record = _frame_record()
                newer_record["frame"]["frame_id"] = "frame_000005"
                newer_record["frame"]["frame_index"] = 5
                newer_record["frame"]["captured_at_ms"] = 1534
                server.perception.publish_frame(
                    frame_bytes=newer_frame,
                    content_type="image/png",
                    frame_record=newer_record,
                )

                with urlopen(f"{server.url}api/latest", timeout=1.0) as response:
                    stale_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(stale_payload["frame"]["frame_id"], "frame_000005")
                self.assertEqual(stale_payload["overlay"]["status"], "stale")
                self.assertEqual(
                    stale_payload["overlay"]["source_frame_id"], "frame_000004"
                )
                self.assertEqual(stale_payload["overlay"]["frame_lag"], 1)
                self.assertEqual(stale_payload["overlay"]["frame_lag_ms"], 300)

                versioned_url = f"{server.url.rstrip('/')}{payload['frame']['url']}"
                with urlopen(versioned_url, timeout=1.0) as response:
                    self.assertEqual(response.read(), expected_frame)

                with urlopen(f"{server.url}frame", timeout=1.0) as response:
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertEqual(response.read(), newer_frame)

                head_request = Request(f"{server.url}frame", method="HEAD")
                with urlopen(head_request, timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertEqual(int(response.headers["Content-Length"]), len(newer_frame))
                    self.assertEqual(response.read(), b"")

                with urlopen(f"{server.url}perception", timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "text/html")

                with urlopen(f"{server.url}", timeout=1.0) as response:
                    runtime_html = response.read().decode("utf-8")
                self.assertIn("Action view unavailable", runtime_html)
                self.assertNotIn('href="/decision"', runtime_html)

                with urlopen(f"{server.url}favicon.ico", timeout=1.0) as response:
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.read(), b"")
            finally:
                server.stop()

    def test_received_bytes_publish_without_a_source_path(self) -> None:
        jpeg = _image_bytes("JPEG", (18, 12), (10, 20, 30))
        png = _image_bytes("PNG", (7, 9), (1, 2, 3))
        view = PerceptionView(host_name="test-vehicle")
        record = _frame_record()
        view.publish_matched(
            frame_bytes=jpeg, content_type="image/jpeg", frame_record=record,
        )
        payload = json.loads(view.latest_json())
        self.assertEqual(payload["frame"]["width_px"], 18)
        self.assertEqual(payload["frame"]["height_px"], 12)
        self.assertEqual(payload["frame"]["content_type"], "image/jpeg")
        self.assertEqual(payload["overlay"]["status"], "current")
        self.assertEqual(payload["overlay"]["source_frame_id"], "frame_000004")
        served, content_type = view.frame()
        self.assertEqual(served, jpeg)
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(view.frame(record["frame"]["frame_id"]), (jpeg, "image/jpeg"))

        later = _frame_record()
        later["frame"]["frame_id"] = "frame_000009"
        later["frame"]["frame_index"] = 9
        view.publish_matched(frame_bytes=png, content_type="image/png", frame_record=later)
        self.assertEqual(view.frame(), (png, "image/png"))
        self.assertEqual(view.frame("frame_000004"), (jpeg, "image/jpeg"))
        later_payload = json.loads(view.latest_json())
        self.assertEqual(later_payload["frame"]["width_px"], 7)
        self.assertEqual(later_payload["frame"]["height_px"], 9)
        self.assertEqual(later_payload["overlay"]["status"], "current")

    def test_preparing_a_new_image_keeps_the_previous_matched_pair_readable(self) -> None:
        view = PerceptionView(host_name="test-vehicle")
        first = _frame_record()
        first_bytes = _image_bytes("JPEG", (4, 4), (1, 2, 3))
        second = _frame_record()
        second["frame"]["frame_id"] = "second"
        second_bytes = _image_bytes("JPEG", (5, 6), (4, 5, 6))
        view.publish_matched(frame_bytes=first_bytes, content_type="image/jpeg", frame_record=first)
        entered, release = threading.Event(), threading.Event()
        open_image = Image.open

        def prepare_image(source):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("image preparation was not released")
            return open_image(source)

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(Image, "open", side_effect=prepare_image):
            writer = pool.submit(
                view.publish_matched, frame_bytes=second_bytes, content_type="image/jpeg", frame_record=second,
            )
            try:
                self.assertTrue(entered.wait(1))
                previous = json.loads(pool.submit(view.latest_json).result(timeout=1))
                self.assertEqual(previous["frame"]["frame_id"], first["frame"]["frame_id"])
                self.assertEqual(previous["overlay"]["status"], "current")
                self.assertEqual(pool.submit(view.frame).result(timeout=1), (first_bytes, "image/jpeg"))
            finally:
                release.set()
            writer.result(timeout=1)
        latest = json.loads(view.latest_json())
        self.assertEqual(latest["frame"]["frame_id"], "second")
        self.assertEqual(latest["overlay"]["source_frame_id"], "second")
        self.assertEqual(latest["overlay"]["status"], "current")
        self.assertEqual(view.frame(), (second_bytes, "image/jpeg"))
        self.assertEqual(view.frame(first["frame"]["frame_id"]), (first_bytes, "image/jpeg"))

    def test_invalid_image_is_refused_and_a_later_image_recovers(self) -> None:
        view = PerceptionView(host_name="test-vehicle")
        good = _image_bytes("JPEG", (4, 4), (9, 9, 9))
        record = _frame_record()
        view.publish_matched(frame_bytes=good, content_type="image/jpeg", frame_record=record)
        for bad in (b"", b"not-an-image"):
            with self.subTest(bad=bad), self.assertRaises((ValueError, OSError)):
                view.publish_frame(
                    frame_bytes=bad, content_type="image/jpeg", frame_record=record,
                )
            self.assertEqual(view.frame()[0], good)
        with self.assertRaises(ValueError):
            view.publish_frame(
                frame_bytes=good, content_type="image/gif", frame_record=record,
            )
        self.assertEqual(view.frame()[0], good)
        replacement = _image_bytes("JPEG", (5, 6), (3, 3, 3))
        next_record = _frame_record()
        next_record["frame"]["frame_id"] = "frame_000008"
        next_record["frame"]["frame_index"] = 8
        view.publish_matched(
            frame_bytes=replacement, content_type="image/jpeg", frame_record=next_record,
        )
        self.assertEqual(view.frame(), (replacement, "image/jpeg"))
        self.assertEqual(json.loads(view.latest_json())["frame"]["width_px"], 5)
        self.assertEqual(json.loads(view.latest_json())["frame"]["height_px"], 6)
        self.assertEqual(json.loads(view.latest_json())["overlay"]["status"], "current")

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

    def test_view_health_rejects_a_different_worker_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = RuntimeViewServer(
                host_name="test-vehicle",
                automation_dir=root,
                port=0,
                run_id="run-current",
                worker_pid=4242,
                replay_runner_factory=ImageReplayRunner,
            ).start()
            try:
                status = get_perception_view_status(
                    root,
                    expected_run_id="run-other",
                    expected_worker_pid=4242,
                )
                self.assertFalse(status["available"])
                self.assertEqual(status["status"], "stale")
                self.assertIn("run_id", status["reason"])

                status = get_perception_view_status(
                    root,
                    expected_run_id="run-current",
                    expected_worker_pid=9999,
                )
                self.assertFalse(status["available"])
                self.assertEqual(status["status"], "stale")
                self.assertIn("pid", status["reason"])
            finally:
                server.stop()


def _image_bytes(fmt: str, size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _frame_record() -> dict:
    return {
        "frame": {
            "frame_id": "frame_000004",
            "frame_index": 4,
            "captured_at_ms": 1234,
            "completed_at_ms": 1242,
        },
        "duration_ms": 17,
        "timing": {
            "duration_ms": 17,
            "steps": [{"step": "perception", "duration_ms": 8, "plugins": []}],
        },
        "sensor_frame": {
            "readings": {
                "front_camera": {
                    "metadata": {"content_type": "image/png"},
                }
            }
        },
        "perception": {
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
        },
        "memory": None,
        "control": {"steering": 0.0, "throttle": 0.0},
        "engine": {"engine": "idle"},
    }


if __name__ == "__main__":
    unittest.main(verbosity=2)
