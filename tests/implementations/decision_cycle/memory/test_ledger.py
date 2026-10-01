from __future__ import annotations

import json
import unittest

from autonomy.decision_cycle.memory.evidence import MemoryProvenance, RetainedEvidence
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from implementations.decision_cycle.memory.bounded_evidence.ledger import (
    LEDGER_SCHEMA,
    EvidenceLedger,
    LedgerBounds,
    bounds_from_config,
    detach_ledger,
    empty_ledger,
)


class EvidenceLedgerTests(unittest.TestCase):
    def bounds(self) -> LedgerBounds:
        return LedgerBounds(max_records=4, max_age_ms=2_000, eviction_policy="oldest_first")

    def retained(self, record_id: str = "rec_1") -> RetainedEvidence:
        return RetainedEvidence(
            record_id=record_id,
            kind="floor_boundary",
            label="first-hit boundary",
            confidence=0.8,
            provenance=MemoryProvenance(
                observation_id="obs_1",
                evidence_id="floor_boundary_000",
                coordinate_frame="image",
                observed_at_ms=100,
                updated_at_ms=150,
                source_plugin_id="floor-plane-v0",
                frame_id="donkey_frame_000100",
            ),
            location=ViewLocation(frame="image", zone="center", bbox_xyxy_norm=(0.4, 0.5, 0.6, 0.9)),
            properties={"width_fraction": 0.2},
        )

    def ledger(self) -> EvidenceLedger:
        return EvidenceLedger(
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

    def test_ledger_serializes_detached_and_round_trips(self) -> None:
        ledger = self.ledger()

        payload = ledger.to_dict()
        json.dumps(payload)
        self.assertEqual(payload["schema"], LEDGER_SCHEMA)
        self.assertEqual(payload["record_count"], 1)
        payload["records"][0]["properties"]["width_fraction"] = 0.9
        payload["metadata"]["source"] = "mutated"
        self.assertEqual(ledger.records[0].properties["width_fraction"], 0.2)
        self.assertEqual(ledger.metadata["source"], "unit-test")

        restored = EvidenceLedger.from_dict(ledger.to_dict())
        self.assertEqual(restored.memory_id, "mem_1")
        self.assertEqual(restored.records[0].record_id, "rec_1")
        self.assertEqual(restored.bounds.max_age_ms, 2_000)
        self.assertEqual(restored.bounds.max_property_bytes, 4_096)
        self.assertEqual(restored.bounds.max_serialized_bytes, 262_144)

    def test_detach_ledger_isolates_nested_mutation(self) -> None:
        original = self.ledger()

        detached = detach_ledger(original)
        detached.records[0].properties["width_fraction"] = 0.5
        detached.metadata["source"] = "mutated"

        self.assertEqual(original.records[0].properties["width_fraction"], 0.2)
        self.assertEqual(original.metadata["source"], "unit-test")

    def test_empty_ledger(self) -> None:
        empty = empty_ledger(memory_id="mem_empty", epoch_id="epoch_1", bounds=self.bounds(), created_at_ms=1)

        self.assertEqual(empty.health, "empty")
        self.assertEqual(empty.record_count, 0)


    def test_bounds_come_from_plugin_config(self) -> None:
        bounds = bounds_from_config({"max_records": 8, "max_age_ms": None})

        self.assertEqual(bounds.max_records, 8)
        self.assertIsNone(bounds.max_age_ms)
        self.assertEqual(bounds.eviction_policy, "oldest_first")
        with self.assertRaisesRegex(ValueError, "max_records"):
            LedgerBounds(max_records=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
