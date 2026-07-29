"""avoid_recent_obstruction reference plugin tests (M006-04)."""

from __future__ import annotations

import unittest

from autonomy.decision.decision_data import build_decision_data_source
from autonomy.decision.memory import (
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
    empty_memory_snapshot,
    error_memory_snapshot,
    unavailable_memory_snapshot,
)
from autonomy.perception import ViewLocation
from implementations.decision.proposals.avoid_recent_obstruction import propose


def _bounds() -> MemoryBounds:
    return MemoryBounds(max_records=16, max_age_ms=10_000)


def _record(
    *,
    kind: str = "floor_boundary",
    zone: str = "left",
    frame_id: str = "frame_001",
    updated_at_ms: int = 1000,
    confidence: float = 0.8,
    record_id: str = "thing:1:a",
    bbox: tuple[float, float, float, float] | None = (0.0, 0.0, 0.2, 0.5),
    location_frame: str = "image",
) -> RetainedEvidence:
    location = None
    if zone is not None or bbox is not None:
        location = ViewLocation(
            frame=location_frame,
            zone=zone,
            bbox_xyxy_norm=bbox,
        )
    return RetainedEvidence(
        record_id=record_id,
        kind=kind,
        label=kind,
        confidence=confidence,
        provenance=MemoryProvenance(
            observation_id="obs",
            evidence_id="ev",
            coordinate_frame=location_frame,
            observed_at_ms=updated_at_ms,
            updated_at_ms=updated_at_ms,
            source_plugin_id="src",
            frame_id=frame_id,
        ),
        location=location,
        properties={},
    )


def _source(records: tuple[RetainedEvidence, ...], *, now: int = 1000, frame: str = "frame_001"):
    if records:
        snap = MemorySnapshot(
            memory_id="m",
            epoch_id="e",
            health="healthy",
            bounds=_bounds(),
            created_at_ms=now,
            records=records,
            implementation_id="bounded_evidence",
        )
    else:
        snap = empty_memory_snapshot(
            memory_id="m",
            epoch_id="e",
            created_at_ms=now,
            bounds=_bounds(),
            implementation_id="bounded_evidence",
        )
    return build_decision_data_source(
        frame_id=frame, frame_index=1, timestamp_ms=now, memory=snap
    )


class AvoidRecentObstructionTests(unittest.TestCase):
    def test_fresh_left_floor_boundary(self) -> None:
        p = propose(_source((_record(zone="left", frame_id="frame_001"),)))
        self.assertEqual(p.lifecycle, "fresh")
        self.assertIsNotNone(p.command)
        assert p.command is not None
        self.assertGreater(p.command.steering, 0)

    def test_right_obstacle_retained(self) -> None:
        p = propose(
            _source(
                (_record(kind="obstacle", zone="right", frame_id="old", updated_at_ms=500),),
                now=1000,
                frame="frame_002",
            )
        )
        self.assertEqual(p.lifecycle, "retained")
        assert p.command is not None
        self.assertLess(p.command.steering, 0)

    def test_obstruction_evidence_kind_accepted(self) -> None:
        p = propose(
            _source((_record(kind="obstruction_evidence", zone="left"),))
        )
        self.assertEqual(p.lifecycle, "fresh")

    def test_fresh_beats_stale_higher_confidence(self) -> None:
        stale = _record(
            zone="right",
            frame_id="old",
            updated_at_ms=0,
            confidence=0.99,
            record_id="thing:1:stale",
        )
        fresh = _record(
            zone="left",
            frame_id="frame_002",
            updated_at_ms=2000,
            confidence=0.5,
            record_id="thing:1:fresh",
        )
        p = propose(_source((stale, fresh), now=2000, frame="frame_002"))
        self.assertEqual(p.lifecycle, "fresh")
        assert p.command is not None
        self.assertGreater(p.command.steering, 0)

    def test_stale_only(self) -> None:
        p = propose(
            _source(
                (_record(frame_id="old", updated_at_ms=0),),
                now=5000,
                frame="frame_009",
            )
        )
        self.assertEqual(p.lifecycle, "stale")
        self.assertIsNone(p.command)

    def test_future_dated_provenance(self) -> None:
        p = propose(
            _source(
                (_record(updated_at_ms=5000, frame_id="frame_001"),),
                now=1000,
            )
        )
        self.assertEqual(p.lifecycle, "error")
        self.assertEqual(p.reason, "future_dated_provenance")

    def test_non_accepted_kind_inactive(self) -> None:
        p = propose(_source((_record(kind="surface", zone="left"),)))
        self.assertEqual(p.lifecycle, "inactive")

    def test_non_image_location_incompatible(self) -> None:
        p = propose(
            _source(
                (
                    _record(
                        kind="floor_boundary",
                        zone="left",
                        location_frame="map",
                    ),
                )
            )
        )
        self.assertEqual(p.lifecycle, "incompatible")

    def test_memory_unavailable_missing_input(self) -> None:
        snap = unavailable_memory_snapshot(
            memory_id="m",
            epoch_id="e",
            created_at_ms=1,
            bounds=_bounds(),
            implementation_id="bounded_evidence",
            reason="gone",
        )
        source = build_decision_data_source(
            frame_id="frame_001", frame_index=0, timestamp_ms=1, memory=snap
        )
        p = propose(source)
        self.assertEqual(p.lifecycle, "missing_input")

    def test_memory_error_missing_input(self) -> None:
        snap = error_memory_snapshot(
            memory_id="m",
            epoch_id="e",
            created_at_ms=1,
            bounds=_bounds(),
            implementation_id="bounded_evidence",
            error="boom",
        )
        source = build_decision_data_source(
            frame_id="frame_001", frame_index=0, timestamp_ms=1, memory=snap
        )
        p = propose(source)
        self.assertEqual(p.lifecycle, "missing_input")

    def test_empty_memory_inactive(self) -> None:
        p = propose(_source(()))
        self.assertEqual(p.lifecycle, "inactive")

    def test_center_band_inactive(self) -> None:
        p = propose(
            _source(
                (
                    _record(
                        zone="center",
                        bbox=(0.45, 0.0, 0.55, 1.0),
                    ),
                )
            )
        )
        self.assertEqual(p.lifecycle, "inactive")

    def test_fresh_center_does_not_fall_back_to_retained_side(self) -> None:
        # Fresh pool exists (center-band cue only). Must not fall back to older retained left.
        fresh_center = _record(
            zone="center",
            bbox=(0.45, 0.0, 0.55, 1.0),
            frame_id="frame_002",
            updated_at_ms=2000,
            confidence=0.5,
            record_id="thing:1:center",
        )
        retained_left = _record(
            zone="left",
            frame_id="old",
            updated_at_ms=1000,
            confidence=0.99,
            record_id="thing:1:left",
        )
        p = propose(
            _source((fresh_center, retained_left), now=2000, frame="frame_002")
        )
        self.assertEqual(p.lifecycle, "inactive")
        self.assertIsNone(p.command)

    def test_uppercase_zone_is_not_exact_lateral_cue(self) -> None:
        p = propose(
            _source(
                (
                    _record(
                        zone="LEFT",
                        bbox=None,
                        frame_id="frame_001",
                        updated_at_ms=1000,
                    ),
                ),
                now=1000,
                frame="frame_001",
            )
        )
        self.assertEqual(p.lifecycle, "inactive")
        self.assertIsNone(p.command)

    def test_center_without_bbox_never_enters_freshness(self) -> None:
        # No lateral cue → not an accepted candidate; do not emit stale/future paths.
        stale = propose(
            _source(
                (
                    _record(
                        zone="center",
                        bbox=None,
                        frame_id="old",
                        updated_at_ms=0,
                    ),
                ),
                now=5000,
                frame="current",
            )
        )
        self.assertEqual(stale.lifecycle, "inactive")
        self.assertIsNone(stale.command)

        future = propose(
            _source(
                (
                    _record(
                        zone="center",
                        bbox=None,
                        frame_id="old",
                        updated_at_ms=5000,
                    ),
                ),
                now=1000,
                frame="current",
            )
        )
        self.assertEqual(future.lifecycle, "inactive")
        self.assertNotEqual(future.reason, "future_dated_provenance")

    def test_ready_malformed_capabilities_error(self) -> None:
        from autonomy.decision.decision_data import ready_envelope

        snap = MemorySnapshot(
            memory_id="m",
            epoch_id="e",
            health="healthy",
            bounds=_bounds(),
            created_at_ms=1000,
            records=(_record(),),
            implementation_id="bounded_evidence",
        )
        source = build_decision_data_source(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=1000,
            memory=snap,
            capabilities=ready_envelope("not-a-dict", updated_at_ms=1000),
        )
        p = propose(source)
        self.assertEqual(p.lifecycle, "error")
        self.assertEqual(p.reason, "invalid_capabilities")

    def test_capabilities_unavailable_uses_configured_magnitude(self) -> None:
        from autonomy.decision.decision_data import unavailable_envelope

        snap = MemorySnapshot(
            memory_id="m",
            epoch_id="e",
            health="healthy",
            bounds=_bounds(),
            created_at_ms=1000,
            records=(_record(),),
            implementation_id="bounded_evidence",
        )
        source = build_decision_data_source(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=1000,
            memory=snap,
            capabilities=unavailable_envelope(
                "stage_not_configured", updated_at_ms=1000
            ),
        )
        p = propose(source, steer_magnitude=0.4)
        self.assertEqual(p.lifecycle, "fresh")
        assert p.command is not None
        self.assertAlmostEqual(p.command.steering, 0.4)
        self.assertIn("capabilities_not_ready", p.assumptions)

    def test_ready_capabilities_invalid_max_abs_steering(self) -> None:
        from autonomy.decision.decision_data import (
            default_capabilities,
            ready_envelope,
        )

        snap = MemorySnapshot(
            memory_id="m",
            epoch_id="e",
            health="healthy",
            bounds=_bounds(),
            created_at_ms=1000,
            records=(_record(),),
            implementation_id="bounded_evidence",
        )
        # NaN cannot enter a ready envelope (strict JSON freeze rejects it first).
        for bad_max in (0, -0.1, 1.5, "high", None):
            with self.subTest(max_abs_steering=bad_max):
                caps = default_capabilities()
                caps["max_abs_steering"] = bad_max
                source = build_decision_data_source(
                    frame_id="frame_001",
                    frame_index=0,
                    timestamp_ms=1000,
                    memory=snap,
                    capabilities=ready_envelope(caps, updated_at_ms=1000),
                )
                p = propose(source)
                self.assertEqual(p.lifecycle, "error")
                self.assertEqual(p.reason, "invalid_capabilities")
                self.assertIsNone(p.command)


if __name__ == "__main__":
    unittest.main()
