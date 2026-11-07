from __future__ import annotations

import unittest
from copy import deepcopy

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.memory.shared.evidence_ledger.ledger import (
    serialized_ledger_bytes,
)
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from implementations.decision_cycle.memory.shared.evidence_ledger.reduction import (
    CONFLICT_POLICY,
    BoundedEvidenceReducer,
    json_values_equal,
    location_geometry_signature,
    namespaced_record_id,
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
    confidence: float = 0.9,
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
