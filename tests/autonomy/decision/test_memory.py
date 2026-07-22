from __future__ import annotations

import json
import unittest

from autonomy.decision import (
    MEMORY_SNAPSHOT_SCHEMA,
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
    empty_memory_snapshot,
    error_memory_snapshot,
    unavailable_memory_snapshot,
)
from autonomy.perception import ViewLocation


class MemoryContractTests(unittest.TestCase):
    def bounds(self) -> MemoryBounds:
        return MemoryBounds(max_records=4, max_age_ms=2_000, eviction_policy="oldest_first")

    def provenance(self) -> MemoryProvenance:
        return MemoryProvenance(
            observation_id="obs_1",
            evidence_id="floor_boundary_000",
            coordinate_frame="image",
            observed_at_ms=100,
            updated_at_ms=150,
            source_plugin_id="floor-plane-v0",
            frame_id="donkey_frame_000100",
        )

    def retained(self, record_id: str = "rec_1") -> RetainedEvidence:
        return RetainedEvidence(
            record_id=record_id,
            kind="floor_boundary",
            label="first-hit boundary",
            confidence=0.8,
            provenance=self.provenance(),
            location=ViewLocation(
                frame="image",
                zone="center",
                bbox_xyxy_norm=(0.4, 0.5, 0.6, 0.9),
            ),
            properties={"width_fraction": 0.2},
        )

    def test_snapshot_serializes_detached_and_round_trips(self) -> None:
        snapshot = MemorySnapshot(
            memory_id="mem_1",
            epoch_id="epoch_a",
            health="healthy",
            bounds=self.bounds(),
            created_at_ms=200,
            records=(self.retained(),),
            summary=("retained_count=1",),
            implementation_id="bounded_evidence",
            metadata={"source": "unit-test"},
        )

        payload = snapshot.to_dict()
        json.dumps(payload)
        self.assertEqual(payload["schema"], MEMORY_SNAPSHOT_SCHEMA)
        self.assertEqual(payload["record_count"], 1)
        self.assertEqual(
            payload["records"][0]["provenance"]["frame_id"],
            "donkey_frame_000100",
        )

        payload["records"][0]["properties"]["width_fraction"] = 0.9
        payload["metadata"]["source"] = "mutated"
        self.assertEqual(snapshot.records[0].properties["width_fraction"], 0.2)
        self.assertEqual(snapshot.metadata["source"], "unit-test")

        restored = MemorySnapshot.from_dict(snapshot.to_dict())
        self.assertEqual(restored.memory_id, "mem_1")
        self.assertEqual(restored.records[0].record_id, "rec_1")
        self.assertEqual(restored.records[0].location.zone, "center")
        self.assertEqual(restored.bounds.max_age_ms, 2_000)
        self.assertEqual(restored.bounds.max_property_bytes, 4_096)
        self.assertEqual(restored.bounds.max_serialized_bytes, 262_144)

    def test_detach_memory_snapshot_isolates_nested_mutation(self) -> None:
        from autonomy.decision import detach_memory_snapshot

        original = MemorySnapshot(
            memory_id="mem_1",
            epoch_id="epoch_a",
            health="healthy",
            bounds=self.bounds(),
            created_at_ms=200,
            records=(self.retained(),),
            metadata={"source": "unit-test"},
        )
        detached = detach_memory_snapshot(original)
        detached.records[0].properties["width_fraction"] = 0.5
        detached.metadata["source"] = "mutated"
        self.assertEqual(original.records[0].properties["width_fraction"], 0.2)
        self.assertEqual(original.metadata["source"], "unit-test")

    def test_empty_unavailable_and_error_factories(self) -> None:
        empty = empty_memory_snapshot(
            memory_id="mem_empty",
            epoch_id="epoch_1",
            bounds=self.bounds(),
            created_at_ms=1,
        )
        unavailable = unavailable_memory_snapshot(
            memory_id="mem_unavail",
            epoch_id="epoch_1",
            bounds=self.bounds(),
            created_at_ms=2,
            reason="not_activated",
        )
        failed = error_memory_snapshot(
            memory_id="mem_err",
            epoch_id="epoch_1",
            bounds=self.bounds(),
            created_at_ms=3,
            error="reducer_failed",
        )

        self.assertEqual(empty.health, "empty")
        self.assertEqual(empty.record_count, 0)
        self.assertEqual(unavailable.health, "unavailable")
        self.assertIn("not_activated", unavailable.summary[0])
        self.assertEqual(failed.health, "error")
        self.assertEqual(failed.error, "reducer_failed")

    def test_rejects_over_capacity_and_invalid_health_pairs(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_records"):
            MemorySnapshot(
                memory_id="mem_1",
                epoch_id="epoch_a",
                health="healthy",
                bounds=MemoryBounds(max_records=1),
                created_at_ms=1,
                records=(self.retained("a"), self.retained("b")),
            )

        with self.assertRaisesRegex(ValueError, "empty memory"):
            MemorySnapshot(
                memory_id="mem_1",
                epoch_id="epoch_a",
                health="empty",
                bounds=self.bounds(),
                created_at_ms=1,
                records=(self.retained(),),
            )

        with self.assertRaisesRegex(ValueError, "healthy memory"):
            MemorySnapshot(
                memory_id="mem_1",
                epoch_id="epoch_a",
                health="healthy",
                bounds=self.bounds(),
                created_at_ms=1,
                records=(),
            )

        with self.assertRaisesRegex(ValueError, "non-empty error"):
            MemorySnapshot(
                memory_id="mem_1",
                epoch_id="epoch_a",
                health="error",
                bounds=self.bounds(),
                created_at_ms=1,
                records=(),
                error="",
            )

    def test_confidence_and_identifiers_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "confidence"):
            RetainedEvidence(
                record_id="rec",
                kind="floor_boundary",
                label="boundary",
                confidence=float("nan"),
                provenance=self.provenance(),
            )

        with self.assertRaisesRegex(ValueError, "record_id"):
            RetainedEvidence(
                record_id="  ",
                kind="floor_boundary",
                label="boundary",
                confidence=0.5,
                provenance=self.provenance(),
            )

        with self.assertRaisesRegex(ValueError, "max_records"):
            MemoryBounds(max_records=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
