"""The shared evidence ledger reduction: what it retains, for how long, under which id,
and how it resolves sightings that conflict."""

from __future__ import annotations

import unittest
from copy import deepcopy

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from implementations.decision_cycle.memory.shared.evidence_ledger.ledger import serialized_ledger_bytes
from implementations.decision_cycle.memory.shared.evidence_ledger.reduction import (
    CONFLICT_POLICY,
    BoundedEvidenceReducer,
    json_values_equal,
    location_geometry_signature,
    namespaced_record_id,
    reduce_evidence,
)


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
        sensor_frame={},
        perception_plugin_id="lightweight_observer",
        summary=("test",),
        things=things,
        signals=signals,
    )


def _thing(
    thing_id: str = "floor_boundary_000",
    *,
    kind: str = "floor_boundary",
    label: str | None = None,
    zone: str = "center",
    confidence: float | None = 0.9,
    frame: str = "image",
    bbox: list[float] | None = None,
    polygon: list[list[float]] | None = None,
    properties: dict | None = None,
    source_plugin_id: str = "floor_plane",
    include_location: bool = True,
) -> dict:
    thing: dict = {
        "thing_id": thing_id,
        "kind": kind,
        "label": label if label is not None else f"{kind}:{thing_id}",
        "confidence": confidence,
        "properties": dict(properties if properties is not None else {"width_fraction": 0.2}),
        "source_plugin_id": source_plugin_id,
    }
    if include_location:
        location: dict = {"frame": frame, "zone": zone}
        if bbox is not None:
            location["bbox_xyxy_norm"] = bbox
        elif polygon is None:
            location["bbox_xyxy_norm"] = [0.4, 0.5, 0.6, 0.9]
        if polygon is not None:
            location["polygon_xy_norm"] = polygon
        thing["location"] = location
    return thing


def _ledger(**kwargs) -> BoundedEvidenceReducer:
    defaults = {"max_records": 8, "max_age_ms": 10_000}
    defaults.update(kwargs)
    return BoundedEvidenceReducer(**defaults)


def _ctx(frame: str, index: int, ts: int) -> DecisionFrameContext:
    return DecisionFrameContext(frame, index, ts)


def _two_sightings(first: dict, second: dict):
    """The ledger after ``first`` is seen on one frame and ``second`` on the next."""

    ledger = _ledger()
    ledger.update(_ctx("f1", 1, 100), _observation("o1", created_at_ms=90, things=(first,)))
    return ledger.update(_ctx("f2", 2, 200), _observation("o2", created_at_ms=190, things=(second,)))


class BoundedEvidenceLedgerTests(unittest.TestCase):
    def test_retains_things_and_signals_with_origin(self) -> None:
        ledger = BoundedEvidenceReducer(max_records=8, max_age_ms=5_000)
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
        state = ledger.update(context, observation)
        self.assertEqual(state.health, "healthy")
        self.assertEqual(state.record_count, 2)
        self.assertEqual(state.plugin_id, "bounded_evidence")
        by_id = {record.record_id: record for record in state.records}
        self.assertIn("thing:1:11:floor_plane:18:floor_boundary_000", by_id)
        self.assertIn("signal:1:20:lightweight_observer:13:floor_visible", by_id)
        thing = by_id["thing:1:11:floor_plane:18:floor_boundary_000"]
        self.assertEqual(thing.origin.observation_id, "obs_1")
        self.assertEqual(thing.origin.frame_id, "frame_1")
        self.assertEqual(thing.origin.source_plugin_id, "floor_plane")
        self.assertEqual(thing.location.zone, "left")

    def test_recurring_evidence_updates_same_slot_without_identity_claim(self) -> None:
        ledger = BoundedEvidenceReducer(max_records=8, max_age_ms=10_000)
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
        self.assertEqual(record.record_id, "thing:1:11:floor_plane:18:floor_boundary_000")
        self.assertEqual(record.location.zone, "right")
        self.assertEqual(record.confidence, 0.95)
        self.assertEqual(record.origin.observation_id, "obs_2")
        self.assertEqual(record.origin.updated_at_ms, 200)

    def test_survives_dropout_until_max_age_then_expires(self) -> None:
        ledger = BoundedEvidenceReducer(max_records=8, max_age_ms=300)
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
        ledger = BoundedEvidenceReducer(max_records=2, max_age_ms=10_000)
        ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing("a"),)),
        )
        ledger.update(
            DecisionFrameContext("f2", 2, 200),
            _observation("o2", created_at_ms=190, things=(_thing("b"),)),
        )
        state = ledger.update(
            DecisionFrameContext("f3", 3, 300),
            _observation("o3", created_at_ms=290, things=(_thing("c"),)),
        )
        ids = {record.record_id for record in state.records}
        self.assertEqual(
            ids,
            {"thing:1:11:floor_plane:1:b", "thing:1:11:floor_plane:1:c"},
        )
        self.assertNotIn("thing:1:11:floor_plane:1:a", ids)
        self.assertEqual(state.metadata.get("capacity_eviction_count"), 1)

    def test_reset_starts_new_empty_epoch(self) -> None:
        ledger = BoundedEvidenceReducer(max_records=4, max_age_ms=5_000)
        first = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing("a"),)),
        )
        self.assertIs(ledger.ledger(), first)
        reset = ledger.reset()
        self.assertEqual(reset.health, "empty")
        self.assertEqual(reset.record_count, 0)
        self.assertNotEqual(reset.epoch_id, first.epoch_id)
        # prior evidence must not reappear after reset
        after = ledger.update(DecisionFrameContext("f2", 2, 200), None)
        self.assertEqual(after.health, "empty")

    def test_record_ids_keep_plugins_with_one_local_id_apart(self) -> None:
        self.assertEqual(namespaced_record_id("thing", "shared", "plugin:a"), "thing:1:8:plugin:a:6:shared")
        for plugins in (("plugin-a", "plugin-b"), ("plugin:a", "plugin_a")):
            with self.subTest(plugins=plugins):
                things = []
                for plugin, zone in zip(plugins, ("left", "right")):
                    thing = _thing("shared", zone=zone)
                    thing["source_plugin_id"] = plugin
                    things.append(thing)
                state = BoundedEvidenceReducer(max_records=8, max_age_ms=5_000).update(
                    DecisionFrameContext("f1", 1, 100),
                    _observation("o1", created_at_ms=90, things=tuple(things)),
                )
                self.assertEqual(
                    {record.record_id: record.location.zone for record in state.records},
                    {namespaced_record_id("thing", "shared", plugin): zone
                     for plugin, zone in zip(plugins, ("left", "right"))},
                )

    def test_reduce_evidence_rehydrates_prior_state_without_keeping_it(self) -> None:
        config = {"max_records": 8, "max_age_ms": 5_000}
        ledger = BoundedEvidenceReducer(**config)
        first = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing("a", zone="left"),)),
        )
        context = DecisionFrameContext("f2", 2, 200)
        observation = _observation("o2", created_at_ms=190, things=(_thing("b", zone="right"),))
        expected = ledger.update(context, observation).to_dict()

        # The same prior state reduces the same way on every call and is left as it was.
        for _ in range(2):
            actual = reduce_evidence(first, context, observation, plugin_id="bounded_evidence", **config)
            self.assertEqual(actual.to_dict(), expected)
        self.assertEqual((first.record_count, first.records[0].record_id), (1, "thing:1:11:floor_plane:1:a"))
        named = reduce_evidence(first, context, observation, plugin_id="example_memory", **config)
        self.assertEqual((named.plugin_id, named.epoch_id), ("example_memory", first.epoch_id))

    def test_skips_false_signals_and_low_confidence_and_keeps_unscored(self) -> None:
        ledger = BoundedEvidenceReducer(
            max_records=8,
            max_age_ms=5_000,
            min_confidence=0.5,
        )
        state = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(
                    _thing("weak", confidence=0.2),
                    _thing("strong", confidence=0.8),
                    _thing("unscored", confidence=None),
                ),
                signals=(
                    {"signal_id": "floor_visible", "value": False, "confidence": 1.0},
                    {"signal_id": "boundary", "value": True, "confidence": 0.9},
                    {"signal_id": "unscored", "value": True, "confidence": None},
                ),
            ),
        )
        confidences = {record.record_id: record.confidence for record in state.records}
        self.assertEqual(
            confidences,
            {
                "thing:1:11:floor_plane:6:strong": 0.8,
                "thing:1:11:floor_plane:8:unscored": None,
                "signal:1:20:lightweight_observer:8:boundary": 0.9,
                "signal:1:20:lightweight_observer:8:unscored": None,
            },
        )

    def test_oversized_properties_are_not_retained(self) -> None:
        ledger = BoundedEvidenceReducer(
            max_records=8,
            max_age_ms=5_000,
            max_property_bytes=64,
        )
        huge = _thing("huge")
        huge["properties"] = {"blob": "x" * 500}
        small = _thing("small")
        state = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(huge, small)),
        )
        ids = {record.record_id for record in state.records}
        self.assertEqual(ids, {"thing:1:11:floor_plane:5:small"})

    def test_namespace_preserves_absent_vs_literal_unknown_and_whitespace(self) -> None:
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

        ledger = BoundedEvidenceReducer(max_records=8, max_age_ms=5_000)
        no_plugin = _thing("shared", zone="left")
        no_plugin["source_plugin_id"] = None
        # Observation without perception_plugin_id keeps source absent.
        unknown_plugin = _thing("shared", zone="right")
        unknown_plugin["source_plugin_id"] = "unknown"
        state = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            Observation(
                observation_id="o1",
                created_at_ms=90,
                sensor_frame={},
                perception_plugin_id=None,
                summary=("test",),
                things=(no_plugin, unknown_plugin),
            ),
        )
        ids = {record.record_id for record in state.records}
        self.assertEqual(ids, {absent, literal_unknown})

    def test_non_json_property_values_are_not_retained(self) -> None:
        ledger = BoundedEvidenceReducer(max_records=8, max_age_ms=5_000)
        bad = _thing("opaque")
        bad["properties"] = {"opaque": object()}
        good = _thing("ok")
        state = ledger.update(
            DecisionFrameContext("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(bad, good)),
        )
        ids = {record.record_id for record in state.records}
        self.assertEqual(ids, {"thing:1:11:floor_plane:2:ok"})


class HelperUnitTests(unittest.TestCase):
    def test_location_geometry_signatures(self) -> None:
        self.assertIsNone(location_geometry_signature(None))
        bare = ViewLocation(frame="image", zone="center")
        self.assertEqual(location_geometry_signature(bare), (False, False))
        bbox = ViewLocation(
            frame="image",
            zone="center",
            bbox_xyxy_norm=(0.1, 0.2, 0.3, 0.4),
        )
        self.assertEqual(location_geometry_signature(bbox), (True, False))
        poly = ViewLocation(
            frame="image",
            zone="center",
            polygon_xy_norm=((0.0, 0.0), (1.0, 0.0), (0.5, 1.0)),
        )
        self.assertEqual(location_geometry_signature(poly), (False, True))
        both = ViewLocation(
            frame="image",
            zone="center",
            bbox_xyxy_norm=(0.1, 0.2, 0.3, 0.4),
            polygon_xy_norm=((0.0, 0.0), (1.0, 0.0), (0.5, 1.0)),
        )
        self.assertEqual(location_geometry_signature(both), (True, True))

    def test_json_numeric_equality_boundaries(self) -> None:
        self.assertTrue(json_values_equal(1, 1.0))
        self.assertTrue(json_values_equal(0, -0.0))
        self.assertFalse(json_values_equal(2**53, 2**53 + 1))
        self.assertTrue(json_values_equal(10**400, 10**400))
        self.assertFalse(json_values_equal(True, 1))
        self.assertFalse(json_values_equal(False, 0))


class ConflictMatrixTests(unittest.TestCase):
    def test_a_second_sighting_updates_its_slot_unless_the_kind_changes(self) -> None:
        compatible = {
            "scalar property": ({"properties": {"score": 1}}, {"properties": {"score": 2}}),
            "confidence, label, zone and bbox": (
                {"confidence": 0.5, "label": "a", "zone": "left"},
                {"confidence": 0.95, "label": "b", "zone": "right", "bbox": [0.1, 0.1, 0.2, 0.2]},
            ),
            "property shape": (
                {"properties": {"tracks": []}},
                {"properties": {"tracks": [{"id": 1}], "events": {"1": "matched"}}},
            ),
        }
        for name, (first, second) in compatible.items():
            with self.subTest(name):
                snap = _two_sightings(_thing(**first), _thing(**second))
                expected = _thing(**second)
                self.assertEqual(snap.record_count, 1)
                record = snap.records[0]
                self.assertEqual(
                    (record.confidence, record.label, record.location.zone, record.properties),
                    (expected["confidence"], expected["label"], expected["location"]["zone"], expected["properties"]),
                )
                self.assertEqual((snap.metadata["conflict_count"], snap.metadata["last_update_conflict_count"]), (0, 0))
                self.assertEqual(snap.metadata["conflict_policy"], CONFLICT_POLICY)

        snap = _two_sightings(_thing(kind="floor_boundary"), _thing(kind="obstacle"))
        self.assertEqual(snap.record_count, 0)
        self.assertEqual((snap.metadata["conflict_count"], snap.metadata["last_update_conflict_count"]), (1, 1))
        self.assertEqual(snap.metadata["last_update_drops"], [{
            "record_id": namespaced_record_id("thing", "floor_boundary_000", "floor_plane"),
            "reason": "kind_changed",
            "action": "removed_prior_and_rejected_current",
        }])

    def test_location_present_absent_and_family_changes(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(),)),
        )
        snap = ledger.update(
            _ctx("f2", 2, 200),
            _observation(
                "o2",
                created_at_ms=190,
                things=(_thing(include_location=False),),
            ),
        )
        self.assertEqual(snap.record_count, 0)
        self.assertEqual(snap.metadata["conflict_count"], 1)

        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(_thing(bbox=[0.1, 0.1, 0.2, 0.2], polygon=None),),
            ),
        )
        # Force bbox-only by not including polygon (default has bbox).
        snap = ledger.update(
            _ctx("f2", 2, 200),
            _observation(
                "o2",
                created_at_ms=190,
                things=(
                    _thing(
                        bbox=None,
                        polygon=[[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]],
                    ),
                ),
            ),
        )
        self.assertEqual(snap.record_count, 0)
        self.assertEqual(snap.metadata["last_update_conflict_count"], 1)

        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(bbox=[0.1, 0.1, 0.2, 0.2]),)),
        )
        snap = ledger.update(
            _ctx("f2", 2, 200),
            _observation(
                "o2",
                created_at_ms=190,
                things=(
                    _thing(
                        bbox=[0.1, 0.1, 0.2, 0.2],
                        polygon=[[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]],
                    ),
                ),
            ),
        )
        self.assertEqual(snap.record_count, 0)

    def test_same_observation_confidence_and_bbox_contradictions(self) -> None:
        # Both tuple orders required by the accepted matrix (not subsumed by score-only).
        for order in ("ab", "ba"):
            with self.subTest(field="confidence", order=order):
                a = _thing(confidence=0.5)
                b = _thing(confidence=0.9)
                things = (a, b) if order == "ab" else (b, a)
                snap = _ledger().update(
                    _ctx("f1", 1, 100),
                    _observation("o1", created_at_ms=90, things=things),
                )
                self.assertEqual(snap.record_count, 0)
                self.assertEqual(snap.metadata["last_update_conflict_count"], 1)

            with self.subTest(field="bbox", order=order):
                a = _thing(bbox=[0.1, 0.1, 0.2, 0.2])
                b = _thing(bbox=[0.3, 0.3, 0.4, 0.4])
                things = (a, b) if order == "ab" else (b, a)
                snap = _ledger().update(
                    _ctx("f1", 1, 100),
                    _observation("o1", created_at_ms=90, things=things),
                )
                self.assertEqual(snap.record_count, 0)
                self.assertEqual(snap.metadata["last_update_conflict_count"], 1)

    def test_same_observation_payload_equal_collapse(self) -> None:
        ledger = _ledger()
        twin = _thing(properties={"score": 1})
        snap = ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(twin, deepcopy(twin))),
        )
        self.assertEqual(snap.record_count, 1)
        self.assertEqual(snap.metadata["last_update_conflict_count"], 0)

    def test_rejected_properties_and_bounded_drop_report(self) -> None:
        ledger = _ledger(max_records=1, max_property_bytes=32)
        rejected = ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(_thing("too_large", properties={"data": "x" * 100}),),
            ),
        )
        self.assertEqual(rejected.record_count, 0)
        self.assertEqual(rejected.metadata["last_update_drops"][0]["reason"], "properties_too_large")
        self.assertEqual(rejected.metadata["last_update_drops"][0]["action"], "not_admitted")

        pressure = ledger.update(
            _ctx("f2", 2, 200),
            _observation(
                "o2",
                created_at_ms=190,
                things=tuple(
                    _thing(str(index), properties={"ok": 1}) for index in range(20)
                ),
            ),
        )
        self.assertEqual(pressure.metadata["last_update_drop_count"], 19)
        self.assertEqual(len(pressure.metadata["last_update_drops"]), 12)
        self.assertEqual(pressure.metadata["last_update_drops_omitted"], 7)

    def test_drop_details_fit_state_byte_limit(self) -> None:
        ledger = _ledger(max_records=1, max_property_bytes=32, max_serialized_bytes=1024)
        state = ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=tuple(
                    _thing(str(index), properties={"data": "x" * 100})
                    for index in range(20)
                ),
            ),
        )
        self.assertLessEqual(serialized_ledger_bytes(state), 1024)
        self.assertEqual(state.metadata["last_update_drop_count"], 20)
        self.assertGreater(state.metadata["last_update_drops_omitted"], 0)

    def test_reset_zeros_conflict_counters(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(kind="floor_boundary"),)),
        )
        ledger.update(
            _ctx("f2", 2, 200),
            _observation("o2", created_at_ms=190, things=(_thing(kind="obstacle"),)),
        )
        reset = ledger.reset()
        self.assertEqual(reset.metadata["conflict_count"], 0)
        self.assertEqual(reset.metadata["last_update_conflict_count"], 0)
        self.assertEqual(reset.metadata["conflict_policy"], CONFLICT_POLICY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
