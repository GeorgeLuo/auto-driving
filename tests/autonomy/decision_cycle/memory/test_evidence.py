from __future__ import annotations

import json
import unittest

from autonomy.decision_cycle.memory.evidence import (
    MemoryOrigin,
    RetainedEvidence,
    detach_evidence,
)
from autonomy.decision_cycle.perception.evidence.values import ViewLocation


class RetainedEvidenceTests(unittest.TestCase):
    def origin(self) -> MemoryOrigin:
        return MemoryOrigin(
            observation_id="obs_1",
            observed_id="floor_boundary_000",
            coordinate_frame="image",
            observed_at_ms=100,
            updated_at_ms=150,
            source_plugin_id="floor_plane",
            frame_id="donkey_frame_000100",
        )

    def retained(self, record_id: str = "rec_1") -> RetainedEvidence:
        return RetainedEvidence(
            record_id=record_id,
            kind="floor_boundary",
            label="first-hit boundary",
            confidence=0.8,
            origin=self.origin(),
            location=ViewLocation(
                frame="image",
                zone="center",
                bbox_xyxy_norm=(0.4, 0.5, 0.6, 0.9),
            ),
            properties={"width_fraction": 0.2},
        )

    def test_record_serializes_detached_and_round_trips(self) -> None:
        record = self.retained()

        payload = record.to_dict()
        json.dumps(payload)
        self.assertEqual(payload["origin"]["frame_id"], "donkey_frame_000100")
        payload["properties"]["width_fraction"] = 0.9
        self.assertEqual(record.properties["width_fraction"], 0.2)

        restored = RetainedEvidence.from_dict(record.to_dict())
        self.assertEqual(restored.record_id, "rec_1")
        self.assertEqual(restored.location.zone, "center")
        self.assertEqual(restored.origin, record.origin)

    def test_detach_evidence_isolates_nested_mutation(self) -> None:
        original = (self.retained("a"), self.retained("b"))

        detached = detach_evidence(original)
        detached[0].properties["width_fraction"] = 0.5

        self.assertEqual(original[0].properties["width_fraction"], 0.2)
        self.assertEqual([record.record_id for record in detached], ["a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
