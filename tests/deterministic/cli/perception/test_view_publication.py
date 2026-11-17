from __future__ import annotations

import unittest
from autonomy.decision_cycle.memory.interface import MemoryReport
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, ViewLocation
from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA, FramePublication
from autonomy.runtime.report import HostReport, ReportCycle, ReportApplication, ReportValues
from cli.automa_cli.shared.memory_report import evidence_publisher, plugin_states
from cli.automa_cli.frontend.perception_view import _publication_payload
from tests.support.memory_fixtures import TWO_PLUGIN_IDS, memory_report, two_plugin_runner


def _publication(
    *, frame_id: str, memory: dict, perception: dict | None = None, duration_ms: int = 12,
) -> FramePublication:
    perception_data = perception or {"things": []}
    cycle = ReportCycle(
        perception=PerceptionText("perception_text", "fixture", "ok", (), tuple(
            PerceivedThing(item["thing_id"], item["kind"], item.get("label", item["kind"]), ViewLocation("image", "center"))
            for item in perception_data["things"]
        )),
        memory=MemoryReport.from_dict(memory) if isinstance(memory, dict) else memory,
    )
    return FramePublication(
        health="healthy",
        read_at_ms=110,
        result_age_ms=0,
        stale_after_ms=1000,
        report=HostReport(
            host_name="piracer",
            run_id="run",
            generation_id="generation",
            frame_id=frame_id,
            frame_index=1,
            timestamp_ms=100,
            published_at_ms=110,
            cycle=cycle,
            application=ReportApplication(False, "observe_only", "idle", 0.0, 0.0),
            values=ReportValues.from_dict({
                "source_id": "fixture", "activation": {"steps": {}}, "stale_after_ms": 1000,
                "delivered_at_ms": None, "delivery_error": None, "receipt": None,
                "timing": {"duration_ms": duration_ms, "steps": []},
            }),
        ),
    )


class MemoryViewPublicationTests(unittest.TestCase):
    def test_physical_frame_record_and_view_payload_carry_memory(self) -> None:
        memory = memory_report({
            "schema": "test_ledger",
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
        })
        recorded = dict(plugin_states(memory))
        self.assertEqual(recorded["evidence_test"]["health"], "healthy")
        self.assertEqual(recorded["evidence_test"]["records"][0]["kind"], "floor_boundary")

        view_payload = _publication_payload(
            host_name="piracer",
            image={
                "frame_id": "donkey_frame_1",
                "frame_index": 1,
                "captured_at_ms": 100,
                "width_px": 640,
                "height_px": 480,
                "url": "/frame?v=donkey_frame_1",
            },
            publication=_publication(
                frame_id="donkey_frame_1",
                memory=memory,
                perception={
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
            ),
            host_telemetry=None,
            evidence=None,
            generated_at_ms=200,
        )
        self.assertEqual(view_payload["schema"], FRAME_PUBLICATION_SCHEMA)
        self.assertEqual(FRAME_PUBLICATION_SCHEMA, "host_frame_publication")
        self.assertEqual(view_payload["report"]["values"]["timing"]["duration_ms"], 12)
        self.assertNotIn("memory", view_payload)
        shown_memory = view_payload["report"]["cycle"]["memory"]
        self.assertEqual(evidence_publisher(shown_memory), "evidence_test")
        shown = dict(plugin_states(shown_memory))
        self.assertEqual(shown["evidence_test"]["epoch_id"], "epoch-2")
        self.assertEqual(
            shown["evidence_test"]["records"][0]["origin"]["frame_id"],
            "donkey_frame_0",
        )

    def test_view_payload_carries_every_plugin_and_the_publisher(self) -> None:
        # The publisher runs first, so the last plugin is not the publisher.
        runner, _shared = two_plugin_runner()
        report = runner.report()
        view_payload = _publication_payload(
            host_name="piracer",
            image={"frame_id": "donkey_frame_1", "frame_index": 1},
            publication=_publication(frame_id="donkey_frame_1", memory=report, perception={"things": []}),
            host_telemetry=None,
            evidence=None,
            generated_at_ms=200,
        )
        shown_memory = view_payload["report"]["cycle"]["memory"]
        self.assertEqual(shown_memory, report.to_dict())
        states = dict(plugin_states(shown_memory))
        self.assertEqual(tuple(states), TWO_PLUGIN_IDS)
        self.assertEqual(states["evidence_test"]["record_count"], 1)
        self.assertEqual(len(states["recording_test"]["records"]), 1)
        self.assertEqual(evidence_publisher(shown_memory), "evidence_test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
