from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import quote

from PIL import Image

from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA, FramePublication
from autonomy.decision_cycle.perception.interface import PerceptionText, PerceptionPluginRun
from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, ViewLocation
from autonomy.runtime.report import HostReport, ReportCycle, ReportApplication, ReportValues
from cli.automa_cli.frontend.perception_view import MAX_BUFFERED_FRAMES, NO_FRAME_ERROR, PerceptionView


class PerceptionViewTests(unittest.TestCase):
    def test_bounded_cache_keeps_the_most_recent_frames(self) -> None:
        frame_bytes = _image_bytes("JPEG", (64, 48), (20, 40, 60))
        view = PerceptionView(host_name="test-vehicle")

        for index in range(MAX_BUFFERED_FRAMES + 1):
            view.publish(
                frame_bytes=frame_bytes,
                publication=_publication(frame_id=f"frame_{index:06d}", frame_index=index),
            )

        self.assertIsNone(view.frame(_image_id("frame_000000")))
        self.assertIsNotNone(view.frame(_image_id("frame_000001")))
        self.assertEqual(json.loads(view.latest_json())["image"]["frame_id"], f"frame_{MAX_BUFFERED_FRAMES:06d}")

    def test_view_keeps_the_published_frame_and_its_findings(self) -> None:
        expected_frame = _image_bytes("JPEG", (64, 48), (20, 40, 60))
        view = PerceptionView(host_name="test-vehicle")
        view.publish(frame_bytes=expected_frame, publication=_publication())

        payload = json.loads(view.latest_json())
        self.assertEqual(payload["schema"], FRAME_PUBLICATION_SCHEMA)
        self.assertEqual(payload["image"]["frame_id"], "frame_000004")
        self.assertEqual(payload["image"]["width_px"], 64)
        self.assertEqual(payload["image"]["content_type"], "image/jpeg")
        self.assertEqual(payload["image"]["url"], f"/frame?v={quote(_image_id('frame_000004'), safe='')}")
        self.assertEqual(payload["report"]["cycle"]["perception"]["things"][0]["thing_id"], "test-region")
        self.assertEqual(
            payload["report"]["cycle"]["perception"]["things"][0]["location"]["polygon_xy_norm"],
            [[0.2, 0.25], [0.6, 0.3], [0.55, 0.75], [0.25, 0.7]],
        )

        newer_frame = _image_bytes("JPEG", (64, 48), (90, 70, 50))
        view.publish(frame_bytes=newer_frame, publication=_publication(frame_id="frame_000005", frame_index=5))
        # The image a payload names stays buffered after a newer one; the latest is the newer.
        self.assertEqual(view.frame(_image_id("frame_000004")), (expected_frame, "image/jpeg"))
        self.assertEqual(view.frame(), (newer_frame, "image/jpeg"))

    def test_result_age_is_measured_from_the_published_findings(self) -> None:
        view = PerceptionView(host_name="test-vehicle")
        view.publish(frame_bytes=_image_bytes("JPEG", (4, 4), (9, 9, 9)), publication=_publication())

        with patch("cli.automa_cli.frontend.perception_view._timestamp_ms", return_value=1300):
            payload = json.loads(view.latest_json())

        self.assertEqual((payload["read_at_ms"], payload["result_age_ms"]), (1300, 1300 - 1242))

    def test_view_has_nothing_to_serve_before_first_publication(self) -> None:
        view = PerceptionView(host_name="test-vehicle")
        self.assertIsNone(view.latest_json())
        self.assertIsNone(view.frame())
        self.assertEqual(view.unavailable_reason, NO_FRAME_ERROR)


def _image_bytes(fmt: str, size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def _image_id(frame_id: str) -> str:
    return json.dumps(["run", "generation", frame_id], separators=(",", ":"))


def _publication(*, frame_id: str = "frame_000004", frame_index: int = 4) -> FramePublication:
    perception = PerceptionText(
        "perception_text", "test-plugin", "ok", (), (
            PerceivedThing("test-region", "region", "test region", ViewLocation(
                "image", "near_center", (0.2, 0.25, 0.6, 0.75),
                ((0.2, 0.25), (0.6, 0.3), (0.55, 0.75), (0.25, 0.7))), 0.8),
            PerceivedThing("front_camera_frame", "sensor_frame", "front camera frame", ViewLocation(
                "image", "full_frame", (0, 0, 1, 1)), 1.0, {"width_px": 64, "height_px": 48}),
        ), (PerceptionPluginRun("test-plugin", "ok", 8, 0, 2),),
    )
    cycle = ReportCycle(perception=perception)
    return FramePublication(
        health="healthy",
        read_at_ms=1242,
        result_age_ms=0,
        stale_after_ms=1000,
        report=HostReport(
            host_name="test-vehicle",
            run_id="run",
            generation_id="generation",
            frame_id=frame_id,
            frame_index=frame_index,
            timestamp_ms=1234,
            published_at_ms=1242,
            cycle=cycle,
            application=ReportApplication(False, "observe_only", "idle", 0.0, 0.0),
            values=ReportValues.from_dict({
                "source_id": "fixture",
                "activation": {"steps": {}},
                "stale_after_ms": 1000,
                "delivered_at_ms": None, "delivery_error": None, "receipt": None,
                "timing": {
                    "duration_ms": 17,
                    "steps": [{"step": "perception", "duration_ms": 8, "plugins": []}],
                },
            }),
        ),
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
