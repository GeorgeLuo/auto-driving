from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision import (
    ActivatedMemoryStage,
    DecisionFrameContext,
    DecisionStages,
    DecisionCycle,
    Observation,
    read_memory_activation,
)
from implementations.memory import (
    DEFAULT_MEMORY_IMPLEMENTATION,
    BoundedEvidenceLedger,
    available_memory_implementation_ids,
    memory_implementation_spec,
)
from implementations.memory.catalog import build_memory_activation_payload


def _observation(
    observation_id: str,
    *,
    created_at_ms: int,
    things: tuple[dict, ...] = (),
    signals: tuple[dict, ...] = (),
) -> Observation:
    return Observation(
        observation_id=observation_id,
        created_at_ms=created_at_ms,
        sensor_snapshot={},
        perception_plugin_id="lightweight_observer",
        summary=("test",),
        things=things,
        signals=signals,
    )


def _thing(
    thing_id: str,
    *,
    kind: str = "floor_boundary",
    zone: str = "center",
    confidence: float = 0.9,
) -> dict:
    return {
        "thing_id": thing_id,
        "kind": kind,
        "label": f"{kind}:{thing_id}",
        "confidence": confidence,
        "location": {
            "frame": "image",
            "zone": zone,
            "bbox_xyxy_norm": [0.4, 0.5, 0.6, 0.9],
        },
        "properties": {"width_fraction": 0.2},
        "source_plugin_id": "floor-plane-v0",
    }


class BoundedEvidenceLedgerTests(unittest.TestCase):
    def test_catalog_exposes_default_implementation(self) -> None:
        self.assertEqual(DEFAULT_MEMORY_IMPLEMENTATION, "bounded_evidence")
        self.assertIn("bounded_evidence", available_memory_implementation_ids())
        entry = memory_implementation_spec("bounded_evidence")
        self.assertEqual(
            entry["implementation_spec"],
            "implementations.memory.bounded_evidence:BoundedEvidenceLedger",
        )

    def test_retains_things_and_signals_with_provenance(self) -> None:
        ledger = BoundedEvidenceLedger(max_records=8, max_age_ms=5_000)
        context = DecisionFrameContext("frame_1", 1, 1_000)
        observation = _observation(
            "obs_1",
            created_at_ms=990,
            things=(_thing("floor_boundary_000", zone="left"),),
            signals=(
                {
                    "signal_id": "floor_visible",
                    "value": True,
                    "confidence": 0.95,
                },
            ),
        )
        snapshot = ledger.update(context, observation)
        self.assertEqual(snapshot.health, "healthy")
        self.assertEqual(snapshot.record_count, 2)
        self.assertEqual(snapshot.implementation_id, "bounded_evidence")
        by_id = {record.record_id: record for record in snapshot.records}
        self.assertIn("thing:1:14:floor-plane-v0:18:floor_boundary_000", by_id)
        self.assertIn("signal:1:20:lightweight_observer:13:floor_visible", by_id)
        thing = by_id["thing:1:14:floor-plane-v0:18:floor_boundary_000"]
        self.assertEqual(thing.provenance.observation_id, "obs_1")
        self.assertEqual(thing.provenance.frame_id, "frame_1")
        self.assertEqual(thing.provenance.source_plugin_id, "floor-plane-v0")
        self.assertEqual(thing.location.zone, "left")
        self.assertFalse(snapshot.metadata["claims_identity"])

    def test_recurring_evidence_updates_same_slot_without_identity_claim(self) -> None:
        ledger = BoundedEvidenceLedger(max_records=8, max_age_ms=10_000)
        first = ledger.update(
            DecisionFrameContext("frame_1", 1, 100),
            _observation(
                "obs_1",
                created_at_ms=90,
                things=(_thing("floor_boundary_000", zone="center", confidence=0.7),),
            ),
        )
        second = ledger.update(
            DecisionFrameContext("frame_2", 2, 200),
            _observation(
                "obs_2",
                created_at_ms=190,
                things=(_thing("floor_boundary_000", zone="right", confidence=0.95),),
            ),
        )
        self.assertEqual(first.record_count, 1)
        self.assertEqual(second.record_count, 1)
        record = second.records[0]
        self.assertEqual(record.record_id, "thing:1:14:floor-plane-v0:18:floor_boundary_000")
        self.assertEqual(record.location.zone, "right")
        self.assertEqual(record.confidence, 0.95)
        self.assertEqual(record.provenance.observation_id, "obs_2")
        self.assertEqual(record.provenance.updated_at_ms, 200)

    def test_survives_dropout_until_max_age_then_expires(self) -> None:
        ledger = BoundedEvidenceLedger(max_records=8, max_age_ms=300)
        ledger.update(
            DecisionFrameContext("frame_1", 1, 1_000),
            _observation(
                "obs_1",
                created_at_ms=990,
                things=(_thing("floor_boundary_000"),),
            ),
        )
        during = ledger.update(DecisionFrameContext("frame_2", 2, 1_200), None)
        self.assertEqual(during.health, "healthy")
        self.assertEqual(during.record_count, 1)
        expired = ledger.update(DecisionFrameContext("frame_3", 3, 1_400), None)
        self.assertEqual(expired.health, "empty")
        self.assertEqual(expired.record_count, 0)

    def test_oldest_first_eviction_at_capacity(self) -> None:
        ledger = BoundedEvidenceLedger(max_records=2, max_age_ms=10_000)
        ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing("a"),)),
        )
        ledger.update(
            DecisionFrameContext("f2", 2, 200),
            _observation("o2", created_at_ms=190, things=(_thing("b"),)),
        )
        snapshot = ledger.update(
            DecisionFrameContext("f3", 3, 300),
            _observation("o3", created_at_ms=290, things=(_thing("c"),)),
        )
        ids = {record.record_id for record in snapshot.records}
        self.assertEqual(
            ids,
            {"thing:1:14:floor-plane-v0:1:b", "thing:1:14:floor-plane-v0:1:c"},
        )
        self.assertNotIn("thing:1:14:floor-plane-v0:1:a", ids)

    def test_reset_starts_new_empty_epoch(self) -> None:
        ledger = BoundedEvidenceLedger(max_records=4, max_age_ms=5_000)
        ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing("a"),)),
        )
        previous_epoch = ledger.snapshot().epoch_id
        reset = ledger.reset()
        self.assertEqual(reset.health, "empty")
        self.assertEqual(reset.record_count, 0)
        self.assertNotEqual(reset.epoch_id, previous_epoch)
        # prior evidence must not reappear after reset
        after = ledger.update(DecisionFrameContext("f2", 2, 200), None)
        self.assertEqual(after.health, "empty")

    def test_activation_loads_through_framework_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text(
                json.dumps(
                    build_memory_activation_payload(
                        config_overrides={"max_records": 4, "max_age_ms": 2_000}
                    )
                ),
                encoding="utf-8",
            )
            stage = ActivatedMemoryStage(read_memory_activation(path))
            observation = _observation(
                "obs_9",
                created_at_ms=90,
                things=(_thing("floor_boundary_001", zone="center"),),
            )
            result = DecisionCycle(
                DecisionStages(
                    observe=lambda context, perception: observation,
                    remember=stage,
                )
            ).run(DecisionFrameContext("frame_9", 9, 100))
            self.assertEqual(result.memory.health, "healthy")
            self.assertEqual(result.memory.record_count, 1)
            self.assertEqual(result.memory.implementation_id, "bounded_evidence")
            self.assertEqual(stage.status()["implementation_id"], "bounded_evidence")

    def test_skips_false_signals_and_low_confidence(self) -> None:
        ledger = BoundedEvidenceLedger(
            max_records=8,
            max_age_ms=5_000,
            min_confidence=0.5,
        )
        snapshot = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(_thing("weak", confidence=0.2), _thing("strong", confidence=0.8)),
                signals=(
                    {"signal_id": "floor_visible", "value": False, "confidence": 1.0},
                    {"signal_id": "boundary", "value": True, "confidence": 0.9},
                ),
            ),
        )
        ids = {record.record_id for record in snapshot.records}
        self.assertEqual(
            ids,
            {
                "thing:1:14:floor-plane-v0:6:strong",
                "signal:1:20:lightweight_observer:8:boundary",
            },
        )

    def test_returned_snapshot_is_detached_from_ledger_state(self) -> None:
        ledger = BoundedEvidenceLedger(max_records=8, max_age_ms=5_000)
        first = ledger.update(
            DecisionFrameContext("frame_1", 1, 1_000),
            _observation(
                "obs_1",
                created_at_ms=990,
                things=(_thing("floor_boundary_000"),),
            ),
        )
        first.records[0].properties["width_fraction"] = 0.99
        first.metadata["policy"] = "mutated"
        second = ledger.snapshot()
        self.assertEqual(second.records[0].properties["width_fraction"], 0.2)
        self.assertEqual(second.metadata["policy"], "bounded_evidence_recency")
        third = ledger.update(DecisionFrameContext("frame_2", 2, 1_100), None)
        self.assertEqual(third.records[0].properties["width_fraction"], 0.2)

    def test_oversized_properties_are_not_retained(self) -> None:
        ledger = BoundedEvidenceLedger(
            max_records=8,
            max_age_ms=5_000,
            max_property_bytes=64,
        )
        huge = _thing("huge")
        huge["properties"] = {"blob": "x" * 500}
        small = _thing("small")
        snapshot = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(huge, small)),
        )
        ids = {record.record_id for record in snapshot.records}
        self.assertEqual(ids, {"thing:1:14:floor-plane-v0:5:small"})

    def test_plugins_with_same_local_id_do_not_collide(self) -> None:
        ledger = BoundedEvidenceLedger(max_records=8, max_age_ms=5_000)
        left = _thing("shared_id", zone="left")
        left["source_plugin_id"] = "plugin-a"
        right = _thing("shared_id", zone="right")
        right["source_plugin_id"] = "plugin-b"
        snapshot = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(left, right)),
        )
        ids = {record.record_id for record in snapshot.records}
        self.assertEqual(
            ids,
            {"thing:1:8:plugin-a:9:shared_id", "thing:1:8:plugin-b:9:shared_id"},
        )
        by_id = {record.record_id: record for record in snapshot.records}
        self.assertEqual(by_id["thing:1:8:plugin-a:9:shared_id"].location.zone, "left")
        self.assertEqual(by_id["thing:1:8:plugin-b:9:shared_id"].location.zone, "right")

    def test_delimiter_containing_plugin_ids_do_not_collide(self) -> None:
        from implementations.memory.bounded_evidence import namespaced_record_id

        left = namespaced_record_id("thing", "shared", "plugin:a")
        right = namespaced_record_id("thing", "shared", "plugin_a")
        self.assertNotEqual(left, right)
        self.assertEqual(left, "thing:1:8:plugin:a:6:shared")
        self.assertEqual(right, "thing:1:8:plugin_a:6:shared")

        ledger = BoundedEvidenceLedger(max_records=8, max_age_ms=5_000)
        a = _thing("shared", zone="left")
        a["source_plugin_id"] = "plugin:a"
        b = _thing("shared", zone="right")
        b["source_plugin_id"] = "plugin_a"
        snapshot = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(a, b)),
        )
        ids = {record.record_id for record in snapshot.records}
        self.assertEqual(ids, {left, right})

    def test_namespace_preserves_absent_vs_literal_unknown_and_whitespace(self) -> None:
        from implementations.memory.bounded_evidence import namespaced_record_id

        absent = namespaced_record_id("thing", "shared", None)
        literal_unknown = namespaced_record_id("thing", "shared", "unknown")
        plain = namespaced_record_id("thing", "shared", "plugin")
        spaced = namespaced_record_id("thing", "shared", " plugin ")
        self.assertNotEqual(absent, literal_unknown)
        self.assertEqual(absent, "thing:0:6:shared")
        self.assertEqual(literal_unknown, "thing:1:7:unknown:6:shared")
        self.assertNotEqual(plain, spaced)
        self.assertEqual(plain, "thing:1:6:plugin:6:shared")
        self.assertEqual(spaced, "thing:1:8: plugin :6:shared")

        ledger = BoundedEvidenceLedger(max_records=8, max_age_ms=5_000)
        no_plugin = _thing("shared", zone="left")
        no_plugin["source_plugin_id"] = None
        # Observation without perception_plugin_id keeps source absent.
        unknown_plugin = _thing("shared", zone="right")
        unknown_plugin["source_plugin_id"] = "unknown"
        snapshot = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            Observation(
                observation_id="o1",
                created_at_ms=90,
                sensor_snapshot={},
                perception_plugin_id=None,
                summary=("test",),
                things=(no_plugin, unknown_plugin),
            ),
        )
        ids = {record.record_id for record in snapshot.records}
        self.assertEqual(ids, {absent, literal_unknown})

    def test_non_json_property_values_are_not_retained(self) -> None:
        ledger = BoundedEvidenceLedger(max_records=8, max_age_ms=5_000)
        bad = _thing("opaque")
        bad["properties"] = {"opaque": object()}
        good = _thing("ok")
        snapshot = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(bad, good)),
        )
        ids = {record.record_id for record in snapshot.records}
        self.assertEqual(ids, {"thing:1:14:floor-plane-v0:2:ok"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
