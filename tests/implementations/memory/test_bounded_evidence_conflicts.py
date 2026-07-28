from __future__ import annotations

import unittest
from copy import deepcopy

from autonomy.decision import DecisionFrameContext, Observation
from autonomy.perception import ViewLocation
from implementations.memory.bounded_evidence import (
    CONFLICT_POLICY,
    BoundedEvidenceLedger,
    json_values_equal,
    location_geometry_signature,
    property_shape,
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
        sensor_snapshot={},
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
    confidence: float = 0.9,
    frame: str = "image",
    bbox: list[float] | None = None,
    polygon: list[list[float]] | None = None,
    properties: dict | None = None,
    source_plugin_id: str = "floor-plane-v0",
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


def _ledger(**kwargs) -> BoundedEvidenceLedger:
    defaults = {"max_records": 8, "max_age_ms": 10_000}
    defaults.update(kwargs)
    return BoundedEvidenceLedger(**defaults)


def _ctx(frame: str, index: int, ts: int) -> DecisionFrameContext:
    return DecisionFrameContext(frame, index, ts)


class HelperUnitTests(unittest.TestCase):
    def test_property_shape_table(self) -> None:
        self.assertEqual(property_shape(None), "null")
        self.assertEqual(property_shape(True), "boolean")
        self.assertEqual(property_shape(1), "number")
        self.assertEqual(property_shape(1.5), "number")
        self.assertEqual(property_shape("x"), "string")
        self.assertEqual(property_shape([]), ("array",))
        self.assertEqual(property_shape([1, "a"]), ("array", "number", "string"))
        self.assertEqual(
            property_shape({"b": 1, "a": True}),
            ("object", (("a", "boolean"), ("b", "number"))),
        )

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
    def test_compatible_scalar_value_change(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(properties={"score": 1}),)),
        )
        snap = ledger.update(
            _ctx("f2", 2, 200),
            _observation("o2", created_at_ms=190, things=(_thing(properties={"score": 2}),)),
        )
        self.assertEqual(snap.record_count, 1)
        self.assertEqual(snap.records[0].properties["score"], 2)
        self.assertEqual(snap.metadata["conflict_count"], 0)
        self.assertEqual(snap.metadata["last_update_conflict_count"], 0)
        self.assertEqual(snap.metadata["conflict_policy"], CONFLICT_POLICY)

    def test_compatible_confidence_label_zone_bbox(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(_thing(confidence=0.5, label="a", zone="left"),),
            ),
        )
        snap = ledger.update(
            _ctx("f2", 2, 200),
            _observation(
                "o2",
                created_at_ms=190,
                things=(
                    _thing(
                        confidence=0.95,
                        label="b",
                        zone="right",
                        bbox=[0.1, 0.1, 0.2, 0.2],
                    ),
                ),
            ),
        )
        self.assertEqual(snap.record_count, 1)
        self.assertEqual(snap.records[0].confidence, 0.95)
        self.assertEqual(snap.records[0].label, "b")
        self.assertEqual(snap.records[0].location.zone, "right")
        self.assertEqual(snap.metadata["last_update_conflict_count"], 0)

    def test_kind_change_invalidates(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(kind="floor_boundary"),)),
        )
        snap = ledger.update(
            _ctx("f2", 2, 200),
            _observation("o2", created_at_ms=190, things=(_thing(kind="obstacle"),)),
        )
        self.assertEqual(snap.record_count, 0)
        self.assertEqual(snap.metadata["conflict_count"], 1)
        self.assertEqual(snap.metadata["last_update_conflict_count"], 1)

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

    def test_property_shape_change(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(properties={"score": 1}),)),
        )
        snap = ledger.update(
            _ctx("f2", 2, 200),
            _observation(
                "o2",
                created_at_ms=190,
                things=(_thing(properties={"score": {"v": 1}}),),
            ),
        )
        self.assertEqual(snap.record_count, 0)
        self.assertEqual(snap.metadata["conflict_count"], 1)

    def test_same_observation_contradiction_order_independent(self) -> None:
        for order in ("ab", "ba"):
            with self.subTest(order=order):
                ledger = _ledger()
                a = _thing(properties={"score": 1})
                b = _thing(properties={"score": 2})
                things = (a, b) if order == "ab" else (b, a)
                snap = ledger.update(
                    _ctx("f1", 1, 100),
                    _observation("o1", created_at_ms=90, things=things),
                )
                self.assertEqual(snap.record_count, 0)
                self.assertEqual(snap.metadata["last_update_conflict_count"], 1)

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

    def test_numeric_payload_equality_boundaries(self) -> None:
        # Each pair is exercised in both tuple orders per the accepted matrix.
        equal_pairs = (
            (1, 1.0),
            (0, -0.0),
            (10**400, 10**400),
        )
        unequal_pairs = (
            (2**53, 2**53 + 1),
            (True, 1),
        )
        for left, right in equal_pairs:
            for order in ("ab", "ba"):
                with self.subTest(pair=(left, right), order=order, equal=True):
                    a = _thing(properties={"score": left})
                    b = _thing(properties={"score": right})
                    things = (a, b) if order == "ab" else (b, a)
                    snap = _ledger().update(
                        _ctx("f1", 1, 100),
                        _observation("o1", created_at_ms=90, things=things),
                    )
                    self.assertEqual(snap.record_count, 1)
                    self.assertEqual(snap.metadata["last_update_conflict_count"], 0)

        for left, right in unequal_pairs:
            for order in ("ab", "ba"):
                with self.subTest(pair=(left, right), order=order, equal=False):
                    prop = "flag" if isinstance(left, bool) or isinstance(right, bool) else "score"
                    a = _thing(properties={prop: left})
                    b = _thing(properties={prop: right})
                    things = (a, b) if order == "ab" else (b, a)
                    snap = _ledger().update(
                        _ctx("f1", 1, 100),
                        _observation("o1", created_at_ms=90, things=things),
                    )
                    self.assertEqual(snap.record_count, 0)
                    self.assertEqual(snap.metadata["last_update_conflict_count"], 1)

    def test_three_unequal_candidates_and_two_slots(self) -> None:
        ledger = _ledger()
        snap = ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(
                    _thing(properties={"score": 1}),
                    _thing(properties={"score": 2}),
                    _thing(properties={"score": 3}),
                ),
            ),
        )
        self.assertEqual(snap.metadata["last_update_conflict_count"], 1)

        ledger = _ledger()
        snap = ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(
                    _thing("a", properties={"score": 1}),
                    _thing("a", properties={"score": 2}),
                    _thing("b", properties={"score": 1}),
                    _thing("b", properties={"score": 2}),
                ),
            ),
        )
        self.assertEqual(snap.metadata["last_update_conflict_count"], 2)
        self.assertEqual(snap.record_count, 0)

    def test_missing_and_false_signal_and_expiry(self) -> None:
        ledger = _ledger(max_age_ms=300)
        ledger.update(
            _ctx("f1", 1, 1000),
            _observation(
                "o1",
                created_at_ms=990,
                things=(_thing(),),
                signals=({"signal_id": "floor_visible", "value": True, "confidence": 0.9},),
            ),
        )
        missing = ledger.update(_ctx("f2", 2, 1100), _observation("o2", created_at_ms=1090))
        self.assertEqual(missing.record_count, 2)
        self.assertEqual(missing.metadata["last_update_conflict_count"], 0)

        false_signal = ledger.update(
            _ctx("f3", 3, 1200),
            _observation(
                "o3",
                created_at_ms=1190,
                things=(_thing(),),
                signals=(
                    {"signal_id": "floor_visible", "value": False, "confidence": 0.9},
                ),
            ),
        )
        # False dropped; signal retained until age; thing refreshed.
        ids = {r.record_id for r in false_signal.records}
        self.assertTrue(any("floor_visible" in i for i in ids))
        self.assertEqual(false_signal.metadata["last_update_conflict_count"], 0)

        expired = ledger.update(_ctx("f4", 4, 2000), None)
        self.assertEqual(expired.record_count, 0)

    def test_post_conflict_empty_slot_readmit(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(kind="floor_boundary"),)),
        )
        conflict = ledger.update(
            _ctx("f2", 2, 200),
            _observation("o2", created_at_ms=190, things=(_thing(kind="obstacle"),)),
        )
        self.assertEqual(conflict.record_count, 0)
        readmit = ledger.update(
            _ctx("f3", 3, 300),
            _observation("o3", created_at_ms=290, things=(_thing(kind="obstacle"),)),
        )
        self.assertEqual(readmit.record_count, 1)
        self.assertEqual(readmit.metadata["last_update_conflict_count"], 0)
        self.assertEqual(readmit.metadata["conflict_count"], 1)

    def test_equal_and_regressing_timestamps_use_invocation_order(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 500),
            _observation("o1", created_at_ms=500, things=(_thing(zone="left"),)),
        )
        equal_ts = ledger.update(
            _ctx("f2", 2, 500),
            _observation("o2", created_at_ms=500, things=(_thing(zone="right"),)),
        )
        self.assertEqual(equal_ts.records[0].location.zone, "right")
        regress = ledger.update(
            _ctx("f3", 3, 100),
            _observation("o3", created_at_ms=100, things=(_thing(zone="center"),)),
        )
        self.assertEqual(regress.records[0].location.zone, "center")
        self.assertEqual(regress.metadata["last_update_conflict_count"], 0)

    def test_snapshot_preserves_last_update_conflict_count(self) -> None:
        ledger = _ledger()
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(kind="floor_boundary"),)),
        )
        conflict = ledger.update(
            _ctx("f2", 2, 200),
            _observation("o2", created_at_ms=190, things=(_thing(kind="obstacle"),)),
        )
        self.assertEqual(conflict.metadata["last_update_conflict_count"], 1)
        self.assertEqual(conflict.metadata["conflict_count"], 1)
        for _ in range(2):
            snap = ledger.snapshot()
            self.assertEqual(snap.metadata["last_update_conflict_count"], 1)
            self.assertEqual(snap.metadata["conflict_count"], 1)
        clean = ledger.update(_ctx("f3", 3, 300), None)
        self.assertEqual(clean.metadata["last_update_conflict_count"], 0)
        self.assertEqual(clean.metadata["conflict_count"], 1)

    def test_capacity_eviction_does_not_increment_conflict(self) -> None:
        ledger = _ledger(max_records=1)
        ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing("a"),)),
        )
        snap = ledger.update(
            _ctx("f2", 2, 200),
            _observation("o2", created_at_ms=190, things=(_thing("b"),)),
        )
        self.assertEqual(snap.metadata["capacity_eviction_count"], 1)
        self.assertEqual(snap.metadata["conflict_count"], 0)
        self.assertEqual(snap.metadata["last_update_conflict_count"], 0)

    def test_expiry_before_compare_admits_without_conflict(self) -> None:
        """Retained older than max_age expires before same-slot compare (matrix row)."""

        ledger = _ledger(max_age_ms=100)
        ledger.update(
            _ctx("f1", 1, 1000),
            _observation(
                "o1",
                created_at_ms=1000,
                things=(_thing(kind="floor_boundary"),),
            ),
        )
        # Past max_age with a structurally incompatible kind: expiry first → empty-slot admit.
        snap = ledger.update(
            _ctx("f2", 2, 1200),
            _observation(
                "o2",
                created_at_ms=1200,
                things=(_thing(kind="obstacle"),),
            ),
        )
        self.assertEqual(snap.record_count, 1)
        self.assertEqual(snap.records[0].kind, "obstacle")
        self.assertEqual(snap.metadata["conflict_count"], 0)
        self.assertEqual(snap.metadata["last_update_conflict_count"], 0)

    def test_capacity_pressure_after_conflict_invalidation(self) -> None:
        """Conflict free-slot then capacity pressure keeps counters separate (matrix row)."""

        ledger = _ledger(max_records=2)
        ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(_thing("a"), _thing("b")),
            ),
        )
        conflict = ledger.update(
            _ctx("f2", 2, 200),
            _observation(
                "o2",
                created_at_ms=190,
                things=(_thing("a", kind="obstacle"),),
            ),
        )
        self.assertEqual(conflict.record_count, 1)
        self.assertEqual(conflict.metadata["conflict_count"], 1)
        self.assertEqual(conflict.metadata["last_update_conflict_count"], 1)
        self.assertEqual(conflict.metadata["capacity_eviction_count"], 0)

        # One retained (b) + two new admits exceeds max_records=2 → capacity eviction only.
        pressure = ledger.update(
            _ctx("f3", 3, 300),
            _observation(
                "o3",
                created_at_ms=290,
                things=(_thing("c"), _thing("d")),
            ),
        )
        self.assertEqual(pressure.record_count, 2)
        self.assertGreaterEqual(pressure.metadata["capacity_eviction_count"], 1)
        self.assertEqual(pressure.metadata["conflict_count"], 1)
        self.assertEqual(pressure.metadata["last_update_conflict_count"], 0)

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

    def test_unrelated_slots_and_cross_plugin(self) -> None:
        ledger = _ledger()
        snap = ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(
                    _thing("a", kind="floor_boundary"),
                    _thing("a", kind="obstacle"),  # contradiction on a
                    _thing("b", kind="floor_boundary"),
                ),
            ),
        )
        ids = {r.record_id for r in snap.records}
        # Slot a conflicted; b admitted.
        self.assertEqual(snap.metadata["last_update_conflict_count"], 1)
        self.assertTrue(any(i.endswith(":1:b") for i in ids))
        self.assertFalse(any(i.endswith(":1:a") for i in ids))

        ledger = _ledger()
        snap = ledger.update(
            _ctx("f1", 1, 100),
            _observation(
                "o1",
                created_at_ms=90,
                things=(
                    _thing("shared", source_plugin_id="plugin-a"),
                    _thing("shared", source_plugin_id="plugin-b"),
                ),
            ),
        )
        self.assertEqual(snap.record_count, 2)
        self.assertEqual(snap.metadata["last_update_conflict_count"], 0)

    def test_caller_mutation_detached(self) -> None:
        ledger = _ledger()
        snap = ledger.update(
            _ctx("f1", 1, 100),
            _observation("o1", created_at_ms=90, things=(_thing(),)),
        )
        snap.metadata["conflict_count"] = 99
        again = ledger.snapshot()
        self.assertEqual(again.metadata["conflict_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
