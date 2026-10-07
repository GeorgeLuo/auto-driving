from __future__ import annotations

import unittest
from cli.automa_cli.memory_report import evidence_publisher, plugin_states
from cli.automa_cli.picar_observation import publication_to_frame_record
from cli.automa_cli.perception_view import (
    PUBLICATION_SCHEMA,
    _publication_payload,
)
from tests.support.memory_fixtures import TWO_PLUGIN_IDS, memory_report, two_plugin_runner


class MemoryViewPublicationTests(unittest.TestCase):
    def test_physical_frame_record_and_view_payload_carry_memory(self) -> None:
        publication = {
            "health": "healthy",
            "duration_ms": 12,
            "preset": "lightweight_observer",
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
                        "location": {
                            "frame": "image",
                            "zone": "left",
                            "bbox_xyxy_norm": [0.1, 0.2, 0.3, 0.4],
                        },
                        "confidence": 0.8,
                    }
                ]
            },
            "memory": memory_report({
                "schema": "bounded_evidence_ledger_v0",
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
                        "origin": {
                            "frame_id": "donkey_frame_0",
                            "observation_id": "obs-0",
                        },
                    }
                ],
            }),
            "control": {"steering": 0.0, "throttle": 0.0},
        }
        frame_record = publication_to_frame_record(publication)
        recorded = dict(plugin_states(frame_record["memory"]))
        self.assertEqual(recorded["bounded_evidence"]["health"], "healthy")
        self.assertEqual(recorded["bounded_evidence"]["records"][0]["kind"], "floor_boundary")

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
        self.assertEqual(view_payload["schema"], PUBLICATION_SCHEMA)
        self.assertEqual(PUBLICATION_SCHEMA, "automa_perception_publication_v2")
        self.assertEqual(evidence_publisher(view_payload["memory"]), "bounded_evidence")
        shown = dict(plugin_states(view_payload["memory"]))
        self.assertEqual(shown["bounded_evidence"]["epoch_id"], "epoch-2")
        self.assertEqual(
            shown["bounded_evidence"]["records"][0]["origin"]["frame_id"],
            "donkey_frame_0",
        )

    def test_view_payload_carries_every_plugin_and_the_publisher(self) -> None:
        # bounded_evidence runs first and publishes, so the last plugin is not
        # the publisher.
        runner, _shared = two_plugin_runner()
        report = runner.report()
        frame_record = publication_to_frame_record(
            {
                "health": "healthy",
                "frame": {"frame_id": "donkey_frame_1", "frame_index": 1, "has_image": True},
                "perception": {"things": []},
                "memory": report,
            }
        )
        view_payload = _publication_payload(
            vehicle_id="piracer",
            frame={"frame_id": "donkey_frame_1", "frame_index": 1},
            perception_record=frame_record,
            generated_at_ms=200,
        )
        self.assertEqual(view_payload["memory"], report)
        states = dict(plugin_states(view_payload["memory"]))
        self.assertEqual(tuple(states), TWO_PLUGIN_IDS)
        self.assertEqual(states["bounded_evidence"]["record_count"], 1)
        self.assertEqual(len(states["recording_test"]["records"]), 1)
        self.assertEqual(evidence_publisher(view_payload["memory"]), "bounded_evidence")


if __name__ == "__main__":
    unittest.main(verbosity=2)
