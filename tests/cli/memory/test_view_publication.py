from __future__ import annotations

import unittest
from pathlib import Path

from cli.automa_cli.physical_observation import publication_to_frame_record
from cli.automa_cli.perception_view import (
    MEMORY_VIEW_HTML_PATH,
    VIEW_HTML_PATH,
    _publication_payload,
)


class MemoryViewPublicationTests(unittest.TestCase):
    def test_memory_and_perception_pages_are_distinct_assets(self) -> None:
        self.assertTrue(VIEW_HTML_PATH.is_file())
        self.assertTrue(MEMORY_VIEW_HTML_PATH.is_file())
        memory_html = MEMORY_VIEW_HTML_PATH.read_text(encoding="utf-8")
        self.assertIn("Memory map", memory_html)
        self.assertIn("key-list", memory_html)
        self.assertIn("key → value", memory_html)
        self.assertIn("Click a key", memory_html)
        self.assertIn("Mapped value", memory_html)
        self.assertIn("record_id", memory_html)
        perception_html = VIEW_HTML_PATH.read_text(encoding="utf-8")
        self.assertIn("Automa Perception", perception_html)
        self.assertIn('href="/memory"', perception_html)
        self.assertIn("Memory map", perception_html)
    def test_physical_frame_record_and_view_payload_carry_memory(self) -> None:
        publication = {
            "health": "healthy",
            "duration_ms": 12,
            "algorithm": "lightweight_observer",
            "frame": {
                "frame_id": "donkey_frame_1",
                "frame_index": 1,
                "captured_at_ms": 100,
                "completed_at_ms": 110,
                "has_image": True,
            },
            "perception": {
                "things": [
                    {
                        "thing_id": "floor_boundary_000",
                        "kind": "floor_boundary",
                        "location": {"frame": "image", "zone": "left", "bbox_xyxy_norm": [0.1, 0.2, 0.3, 0.4]},
                        "confidence": 0.8,
                    }
                ]
            },
            "memory": {
                "schema": "decision_memory_snapshot_v0",
                "health": "healthy",
                "epoch_id": "epoch-2",
                "record_count": 1,
                "records": [
                    {
                        "record_id": "thing:floor_boundary_000",
                        "kind": "floor_boundary",
                        "label": "boundary",
                        "confidence": 0.8,
                        "location": {
                            "frame": "image",
                            "zone": "left",
                            "bbox_xyxy_norm": [0.1, 0.2, 0.3, 0.4],
                        },
                        "provenance": {
                            "frame_id": "donkey_frame_0",
                            "observation_id": "obs-0",
                        },
                    }
                ],
            },
            "control": {"steering": 0.0, "throttle": 0.0},
        }
        frame_record = publication_to_frame_record(publication)
        self.assertEqual(frame_record["memory"]["health"], "healthy")
        self.assertEqual(frame_record["memory"]["records"][0]["kind"], "floor_boundary")

        view_payload = _publication_payload(
            vehicle_id="piracer",
            frame={
                "frame_id": "donkey_frame_1",
                "frame_index": 1,
                "captured_at_ms": 100,
                "width_px": 640,
                "height_px": 480,
                "url": "/frame?v=donkey_frame_1",
            },
            perception_record=frame_record,
            generated_at_ms=200,
        )
        self.assertEqual(view_payload["memory"]["epoch_id"], "epoch-2")
        self.assertEqual(view_payload["memory"]["records"][0]["provenance"]["frame_id"], "donkey_frame_0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
