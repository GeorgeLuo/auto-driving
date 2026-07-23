from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision import (
    MEMORY_ACTIVATION_SCHEMA,
    ActivatedMemoryStage,
    DecisionCycle,
    DecisionFrameContext,
    DecisionStages,
    MemoryBounds,
    MemoryProvenance,
    MemorySnapshot,
    Observation,
    RetainedEvidence,
    empty_memory_snapshot,
    read_memory_activation,
)
from autonomy.perception import ViewLocation


class _RecordingMemory:
    """Test double used only by activation tests."""

    def __init__(
        self,
        *,
        max_records: int = 4,
        max_age_ms: int | None = 1_000,
        eviction_policy: str = "oldest_first",
        fail_on_update: bool = False,
        fail_on_reset: bool = False,
        implementation_id: str = "recording_test",
        max_property_bytes: int | None = 4_096,
        max_serialized_bytes: int | None = 262_144,
        **_ignored,
    ) -> None:
        self.implementation_id = implementation_id
        self.bounds = MemoryBounds(
            max_records=max_records,
            max_age_ms=max_age_ms,
            eviction_policy=eviction_policy,
            max_property_bytes=max_property_bytes,
            max_serialized_bytes=max_serialized_bytes,
        )
        self.fail_on_update = fail_on_update
        self.fail_on_reset = fail_on_reset
        self.epoch = 0
        self.updates = 0
        self._snapshot = self.reset()

    def update(self, context, observation):
        if self.fail_on_update:
            raise RuntimeError("forced-update-failure")
        self.updates += 1
        if observation is None:
            self._snapshot = empty_memory_snapshot(
                memory_id=f"mem-{context.frame_id}",
                epoch_id=f"epoch-{self.epoch}",
                bounds=self.bounds,
                created_at_ms=context.timestamp_ms,
                implementation_id=self.implementation_id,
                summary=("memory_empty=true reason=no_observation",),
            )
            return self._snapshot
        record = RetainedEvidence(
            record_id=f"rec-{observation.observation_id}",
            kind="observation_presence",
            label="observed",
            confidence=1.0,
            provenance=MemoryProvenance(
                observation_id=observation.observation_id,
                evidence_id="observation",
                coordinate_frame="image",
                observed_at_ms=observation.created_at_ms,
                updated_at_ms=context.timestamp_ms,
                frame_id=context.frame_id,
            ),
            location=ViewLocation(frame="image", zone="center"),
        )
        self._snapshot = MemorySnapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id=f"epoch-{self.epoch}",
            health="healthy",
            bounds=self.bounds,
            created_at_ms=context.timestamp_ms,
            records=(record,),
            summary=("retained_count=1",),
            implementation_id=self.implementation_id,
        )
        return self._snapshot

    def reset(self):
        if self.fail_on_reset:
            raise RuntimeError("reset exploded")
        self.epoch += 1
        self._snapshot = empty_memory_snapshot(
            memory_id=f"mem-reset-{self.epoch}",
            epoch_id=f"epoch-{self.epoch}",
            bounds=self.bounds,
            created_at_ms=0,
            implementation_id=self.implementation_id,
        )
        return self._snapshot

    def snapshot(self):
        return self._snapshot


class _OverCapacityMemory(_RecordingMemory):
    """Returns more records than the activation permits."""

    def update(self, context, observation):
        records = tuple(
            RetainedEvidence(
                record_id=f"rec-{index}",
                kind="observation_presence",
                label="observed",
                confidence=1.0,
                provenance=MemoryProvenance(
                    observation_id="obs",
                    evidence_id=f"e-{index}",
                    coordinate_frame="image",
                    observed_at_ms=1,
                    updated_at_ms=1,
                ),
            )
            for index in range(self.bounds.max_records + 1)
        )
        return MemorySnapshot(
            memory_id="bad",
            epoch_id="epoch-x",
            health="healthy",
            bounds=MemoryBounds(max_records=self.bounds.max_records + 1),
            created_at_ms=1,
            records=records,
            implementation_id=self.implementation_id,
        )


class _WeakAgeMemory(_RecordingMemory):
    """Reports a weaker age bound than the activation allows."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Keep internal config as activation requested, but report no age limit.
        self.bounds = MemoryBounds(
            max_records=self.bounds.max_records,
            max_age_ms=None,
            eviction_policy=self.bounds.eviction_policy,
        )


class _MutatingSharedSnapshotMemory(_RecordingMemory):
    """Returns the same snapshot object on successive reads (isolation probe)."""

    def update(self, context, observation):
        super().update(context, observation)
        return self._snapshot

    def snapshot(self):
        return self._snapshot


class _NonJsonPropertyMemory(_RecordingMemory):
    """Returns a healthy snapshot with a non-JSON property value."""

    def update(self, context, observation):
        from autonomy.decision import (
            MemoryProvenance,
            MemorySnapshot,
            RetainedEvidence,
        )

        del observation
        return MemorySnapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id=f"epoch-{self.epoch}",
            health="healthy",
            bounds=self.bounds,
            created_at_ms=context.timestamp_ms,
            records=(
                RetainedEvidence(
                    record_id="rec-opaque",
                    kind="observation_presence",
                    label="opaque",
                    confidence=1.0,
                    provenance=MemoryProvenance(
                        observation_id="obs",
                        evidence_id="opaque",
                        coordinate_frame="image",
                        observed_at_ms=1,
                        updated_at_ms=context.timestamp_ms,
                        frame_id=context.frame_id,
                    ),
                    properties={"opaque": object()},
                ),
            ),
            implementation_id=self.implementation_id,
        )


class _ConfigurableIdMemory(_RecordingMemory):
    """Allows activation to declare a custom implementation_id (including multibyte)."""


class _BrokenStringError(RuntimeError):
    def __str__(self) -> str:
        raise RuntimeError("stringification failed")


class _BrokenStrMemory(_RecordingMemory):
    def __init__(self, **kwargs):
        self._armed = False
        super().__init__(**kwargs)
        self._armed = True

    def update(self, context, observation):
        if self._armed:
            raise _BrokenStringError("payload")
        return super().update(context, observation)

    def reset(self):
        if self._armed:
            raise _BrokenStringError("payload")
        return super().reset()

    def snapshot(self):
        if self._armed:
            raise _BrokenStringError("payload")
        return super().snapshot()


class _SelfContradictingBoundsMemory(_RecordingMemory):
    """Advertises a tight serialized ceiling but returns an oversized empty snapshot."""

    def update(self, context, observation):
        from autonomy.decision import MemoryBounds, empty_memory_snapshot

        del observation
        tight = MemoryBounds(
            max_records=self.bounds.max_records,
            max_age_ms=self.bounds.max_age_ms,
            eviction_policy=self.bounds.eviction_policy,
            max_property_bytes=self.bounds.max_property_bytes or 4_096,
            max_serialized_bytes=512,
        )
        # Inflate epoch so the body exceeds the advertised 512-byte ceiling while
        # still remaining under the default activation ceiling.
        return empty_memory_snapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id="e" + ("x" * 1_200),
            bounds=tight,
            created_at_ms=context.timestamp_ms,
            implementation_id=self.implementation_id,
            summary=("memory_empty=true",),
            metadata={"pad": "y" * 200},
        )


class _NormalizationInflatesSizeMemory(_RecordingMemory):
    """Snapshot fits its short eviction_policy but is rejected for policy mismatch."""

    def update(self, context, observation):
        from autonomy.decision import MemoryBounds, empty_memory_snapshot

        del observation
        short_bounds = MemoryBounds(
            max_records=self.bounds.max_records,
            max_age_ms=self.bounds.max_age_ms,
            eviction_policy="x",
            max_property_bytes=self.bounds.max_property_bytes or 4_096,
            max_serialized_bytes=self.bounds.max_serialized_bytes or 600,
        )
        return empty_memory_snapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id="epoch-1",
            bounds=short_bounds,
            created_at_ms=context.timestamp_ms,
            implementation_id=self.implementation_id,
            summary=("memory_empty=true",),
            metadata={},
        )


class _TighterDeclaredSizeMemory(_RecordingMemory):
    """Same policy label; declared size fields are smaller so normalize grows the body."""

    def update(self, context, observation):
        from autonomy.decision import MemoryBounds, empty_memory_snapshot

        del observation
        tighter = MemoryBounds(
            max_records=self.bounds.max_records,
            max_age_ms=self.bounds.max_age_ms,
            eviction_policy=self.bounds.eviction_policy,
            max_property_bytes=64,
            max_serialized_bytes=512,
        )
        # Pad so declared body is just under 512 and activation-normalized body exceeds 512.
        return empty_memory_snapshot(
            memory_id="m",
            epoch_id="e",
            bounds=tighter,
            created_at_ms=1,
            implementation_id=self.implementation_id,
            summary=("memory_empty=true",),
            metadata={"p": "x" * 144},
        )


class _NearCeilingThenFailMemory(_RecordingMemory):
    """First update returns a near-ceiling empty snapshot; later updates raise."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._updates_seen = 0

    def update(self, context, observation):
        from autonomy.decision import empty_memory_snapshot, serialized_memory_snapshot_bytes

        del observation
        self._updates_seen += 1
        if self._updates_seen >= 2:
            raise RuntimeError("forced-after-near-ceiling")
        # Grow epoch_id until the empty snapshot sits just under the ceiling.
        limit = self.bounds.max_serialized_bytes or 2_000
        epoch = "e"
        snapshot = empty_memory_snapshot(
            memory_id="m",
            epoch_id=epoch,
            bounds=self.bounds,
            created_at_ms=context.timestamp_ms,
            implementation_id=self.implementation_id,
        )
        # Binary-ish growth: expand epoch while still fitting.
        while True:
            candidate_epoch = epoch + ("x" * 32)
            candidate = empty_memory_snapshot(
                memory_id="m",
                epoch_id=candidate_epoch,
                bounds=self.bounds,
                created_at_ms=context.timestamp_ms,
                implementation_id=self.implementation_id,
            )
            size = serialized_memory_snapshot_bytes(candidate)
            if size > limit - 12:
                break
            epoch = candidate_epoch
            snapshot = candidate
        # Fine-tune with single chars.
        while True:
            candidate_epoch = epoch + "y"
            candidate = empty_memory_snapshot(
                memory_id="m",
                epoch_id=candidate_epoch,
                bounds=self.bounds,
                created_at_ms=context.timestamp_ms,
                implementation_id=self.implementation_id,
            )
            size = serialized_memory_snapshot_bytes(candidate)
            if size > limit:
                break
            epoch = candidate_epoch
            snapshot = candidate
        self._snapshot = snapshot
        return snapshot


def _valid_payload() -> dict:
    return {
        "schema": MEMORY_ACTIVATION_SCHEMA,
        "memory": {
            "implementation_id": "recording_test",
            "implementation_spec": (
                "tests.autonomy.decision.test_memory_activation:_RecordingMemory"
            ),
            "implementation_config": {
                "max_records": 4,
                "max_age_ms": 1_000,
                "eviction_policy": "oldest_first",
            },
        },
    }


def _write_payload(root: str, payload: object) -> Path:
    path = Path(root) / "active.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class MemoryActivationTests(unittest.TestCase):
    def test_activation_loads_and_runs_through_decision_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation = read_memory_activation(_write_payload(tmp, _valid_payload()))
            stage = ActivatedMemoryStage(activation)

            context = DecisionFrameContext(
                frame_id="frame_1",
                frame_index=1,
                timestamp_ms=100,
            )
            observation = Observation(
                observation_id="obs_1",
                created_at_ms=90,
                sensor_snapshot={},
                summary=("hello",),
            )
            result = DecisionCycle(
                DecisionStages(remember=stage),
            ).run(context)
            # no observation stage => observation is None on first cycle
            self.assertEqual(result.memory.health, "empty")

            remembered = stage(context, observation)
            self.assertEqual(remembered.health, "healthy")
            self.assertEqual(remembered.record_count, 1)
            self.assertEqual(
                remembered.records[0].provenance.observation_id,
                "obs_1",
            )
            status = stage.status()
            self.assertEqual(status["implementation_id"], "recording_test")
            self.assertEqual(status["update_count"], 2)
            self.assertEqual(status["failure_count"], 0)
            self.assertIsNotNone(status["last_duration_ms"])

    def test_reset_starts_a_new_empty_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, _valid_payload()))
            )
            context = DecisionFrameContext("frame_2", 2, 200)
            observation = Observation("obs_2", 190, {})
            stage.update(context, observation)
            self.assertEqual(stage.last_snapshot.health, "healthy")

            reset = stage.reset()
            self.assertEqual(reset.health, "empty")
            self.assertEqual(reset.record_count, 0)
            self.assertNotEqual(reset.epoch_id, "epoch-1")
            self.assertEqual(stage.snapshot().health, "empty")

    def test_update_failures_become_error_snapshots_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["fail_on_update"] = True
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            snapshot = stage.update(
                DecisionFrameContext("frame_3", 3, 300),
                Observation("obs_3", 290, {}),
            )
            self.assertEqual(snapshot.health, "error")
            self.assertIn("forced-update-failure", snapshot.error or "")
            self.assertEqual(snapshot.record_count, 0)
            self.assertEqual(stage.failure_count, 1)

    def test_framework_rejects_over_capacity_snapshots_as_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_OverCapacityMemory"
            )
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            snapshot = stage.update(
                DecisionFrameContext("frame_4", 4, 400),
                Observation("obs_4", 390, {}),
            )
            self.assertEqual(snapshot.health, "error")
            self.assertIn("max_records", snapshot.error or "")

    def test_framework_rejects_removed_max_age_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_WeakAgeMemory"
            )
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            snapshot = stage.update(
                DecisionFrameContext("frame_5", 5, 500),
                Observation("obs_5", 490, {}),
            )
            self.assertEqual(snapshot.health, "error")
            self.assertIn("max_age_ms", snapshot.error or "")

    def test_framework_detaches_returned_snapshots_from_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_MutatingSharedSnapshotMemory"
            )
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            observation = Observation("obs_6", 590, {})
            first = stage.update(DecisionFrameContext("frame_6", 6, 600), observation)
            first.metadata["tampered"] = True
            if first.records:
                first.records[0].properties["width"] = 9
            second = stage.snapshot()
            self.assertNotIn("tampered", second.metadata)
            if second.records:
                self.assertNotIn("width", second.records[0].properties)

    def test_caller_mutation_does_not_affect_stage_owned_last_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, _valid_payload()))
            )
            returned = stage.update(
                DecisionFrameContext("frame_7", 7, 700),
                Observation("obs_7", 690, {}),
            )
            self.assertIsNot(returned, stage.last_snapshot)
            returned.metadata["caller"] = "mutated"
            if returned.records:
                returned.records[0].properties["extra"] = 1
            owned = stage.last_snapshot
            assert owned is not None
            self.assertNotIn("caller", owned.metadata)
            if owned.records:
                self.assertNotIn("extra", owned.records[0].properties)
            reread = stage.snapshot()
            self.assertIsNot(reread, stage.last_snapshot)
            self.assertNotIn("caller", reread.metadata)

    def test_large_exception_diagnostics_stay_under_serialized_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["fail_on_update"] = True
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 2_000
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            # Force an oversized exception string through the stage boundary.
            stage.implementation.fail_on_update = True
            original_update = stage.implementation.update

            def huge_fail(context, observation):
                del context, observation
                raise RuntimeError("x" * 300_000)

            stage.implementation.update = huge_fail  # type: ignore[method-assign]
            try:
                snapshot = stage.update(
                    DecisionFrameContext("frame_8", 8, 800),
                    Observation("obs_8", 790, {}),
                )
            finally:
                stage.implementation.update = original_update  # type: ignore[method-assign]
            self.assertEqual(snapshot.health, "error")
            from autonomy.decision import (
                DEFAULT_MAX_DIAGNOSTIC_CHARS,
                serialized_memory_snapshot_bytes,
            )

            self.assertLessEqual(serialized_memory_snapshot_bytes(snapshot), 2_000)
            self.assertLessEqual(
                serialized_memory_snapshot_bytes(stage.last_snapshot),  # type: ignore[arg-type]
                2_000,
            )
            # last_error/status must also be bounded (Chase worker publishes this).
            status = stage.status()
            self.assertIsNotNone(status["last_error"])
            self.assertLessEqual(len(status["last_error"]), DEFAULT_MAX_DIAGNOSTIC_CHARS)
            self.assertLessEqual(len(stage.last_error or ""), DEFAULT_MAX_DIAGNOSTIC_CHARS)
            self.assertEqual(status["last_error"], stage.last_error)
            # Status JSON itself must stay modest.
            status_bytes = len(json.dumps(status, sort_keys=True).encode("utf-8"))
            self.assertLess(status_bytes, 4_000)

    def test_error_fallback_ignores_prior_near_ceiling_epoch_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_NearCeilingThenFailMemory"
            )
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 2_000
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            first = stage.update(
                DecisionFrameContext("frame_a", 1, 100),
                Observation("obs_a", 90, {}),
            )
            self.assertEqual(first.health, "empty")
            self.assertGreater(len(first.epoch_id), 1_000)
            # Next update raises; isolation must not leak ValueError from fallback size.
            second = stage.update(
                DecisionFrameContext("frame_b", 2, 200),
                Observation("obs_b", 190, {}),
            )
            self.assertEqual(second.health, "error")
            self.assertTrue(second.epoch_id.startswith("epoch-error-"))
            self.assertLessEqual(len(second.epoch_id), 48)
            from autonomy.decision import serialized_memory_snapshot_bytes

            self.assertLessEqual(serialized_memory_snapshot_bytes(second), 2_000)
            self.assertLessEqual(
                serialized_memory_snapshot_bytes(stage.last_snapshot),  # type: ignore[arg-type]
                2_000,
            )

    def test_reset_failure_preserves_bounded_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            stage.implementation.fail_on_reset = True
            snapshot = stage.reset()
            self.assertEqual(snapshot.health, "empty")
            self.assertIn("reset exploded", snapshot.summary[0])
            self.assertIn("reset exploded", snapshot.metadata.get("reset_error", ""))
            self.assertIn("reset exploded", stage.last_error or "")
            self.assertEqual(stage.status()["last_error"], stage.last_error)

    def test_impossible_max_serialized_bytes_rejected_at_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 1
            with self.assertRaisesRegex(ValueError, "max_serialized_bytes must be >="):
                read_memory_activation(_write_payload(tmp, payload))

    def test_large_eviction_policy_rejected_when_fallback_cannot_fit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 512
            payload["memory"]["implementation_config"]["eviction_policy"] = "p" * 600
            with self.assertRaisesRegex(ValueError, "too small for framework failure"):
                read_memory_activation(_write_payload(tmp, payload))

    def test_near_threshold_bounds_still_isolate_reset_failure(self) -> None:
        """Activation probe must match live timestamp/identity width.

        A policy that fits the worst-case validated shape must still isolate
        reset failures without raising under the same ceiling.
        """
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 512
            # Just under the activation-time capacity limit for these bounds.
            payload["memory"]["implementation_config"]["eviction_policy"] = "p" * 85
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            stage.implementation.fail_on_reset = True
            snapshot = stage.reset()
            self.assertEqual(snapshot.health, "empty")
            self.assertTrue(snapshot.epoch_id.startswith("epoch-reset-failed-"))
            self.assertEqual(len(snapshot.epoch_id.split("-")[-1]), 10)
            from autonomy.decision import serialized_memory_snapshot_bytes

            self.assertLessEqual(serialized_memory_snapshot_bytes(snapshot), 512)
            self.assertIn("reset exploded", stage.last_error or "")

    def test_multibyte_implementation_id_does_not_break_fallback_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            # Multibyte id matching activation/implementation; fallback must use a
            # fixed ASCII marker, not a character-truncated copy of this id.
            multibyte_id = "x" + ("😀" * 100)
            payload["memory"]["implementation_id"] = multibyte_id
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_ConfigurableIdMemory"
            )
            payload["memory"]["implementation_config"]["implementation_id"] = multibyte_id
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 512
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            self.assertEqual(stage.activation.implementation_id, multibyte_id)

            def boom(context, observation):
                del context, observation
                raise RuntimeError("boom")

            original_update = stage.implementation.update
            stage.implementation.update = boom  # type: ignore[method-assign]
            try:
                snapshot = stage.update(
                    DecisionFrameContext("f", 1, 1),
                    Observation("o", 1, {}),
                )
            finally:
                stage.implementation.update = original_update  # type: ignore[method-assign]
            self.assertEqual(snapshot.health, "error")
            self.assertEqual(snapshot.implementation_id, "framework")
            self.assertNotEqual(snapshot.implementation_id, multibyte_id[:48])
            from autonomy.decision import serialized_memory_snapshot_bytes

            self.assertLessEqual(serialized_memory_snapshot_bytes(snapshot), 512)

    def test_broken_exception_str_still_isolates_update_reset_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_BrokenStrMemory"
            )
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            updated = stage.update(
                DecisionFrameContext("f1", 1, 1),
                Observation("o1", 1, {}),
            )
            self.assertEqual(updated.health, "error")
            self.assertIn("unprintable exception", stage.last_error or "")
            self.assertNotIn("stringification failed", stage.last_error or "")

            reset = stage.reset()
            self.assertEqual(reset.health, "empty")
            self.assertIn("unprintable exception", stage.last_error or "")

            snapped = stage.snapshot()
            self.assertEqual(snapped.health, "error")
            self.assertIn("unprintable exception", stage.last_error or "")

    def test_rejects_snapshot_that_exceeds_its_own_serialized_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_SelfContradictingBoundsMemory"
            )
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            snapshot = stage.update(
                DecisionFrameContext("f1", 1, 1),
                Observation("o1", 1, {}),
            )
            self.assertEqual(snapshot.health, "error")
            self.assertIn("declares max_serialized_bytes", snapshot.error or "")

    def test_rejects_when_normalization_would_exceed_activation_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 600
            payload["memory"]["implementation_config"]["eviction_policy"] = "p" * 140
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_NormalizationInflatesSizeMemory"
            )
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            snapshot = stage.update(
                DecisionFrameContext("f1", 1, 1),
                Observation("o1", 1, {}),
            )
            # Policy mismatch is rejected before silent normalize-and-relabel.
            self.assertEqual(snapshot.health, "error")
            self.assertIsNotNone(stage.last_error)
            self.assertIn("eviction_policy", stage.last_error or "")
            from autonomy.decision import serialized_memory_snapshot_bytes

            self.assertLessEqual(serialized_memory_snapshot_bytes(snapshot), 600)

    def test_rejects_when_normalization_increases_size_past_activation_ceiling(self) -> None:
        """Same eviction_policy; tighter declared size fields make normalize grow past limit."""
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 512
            payload["memory"]["implementation_config"]["eviction_policy"] = "oldest_first"
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_TighterDeclaredSizeMemory"
            )
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            snapshot = stage.update(
                DecisionFrameContext("f1", 1, 1),
                Observation("o1", 1, {}),
            )
            self.assertEqual(snapshot.health, "error")
            self.assertIn("normalized memory snapshot", stage.last_error or "")
            from autonomy.decision import serialized_memory_snapshot_bytes

            self.assertLessEqual(serialized_memory_snapshot_bytes(snapshot), 512)

    def test_framework_rejects_non_json_property_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_spec"] = (
                "tests.autonomy.decision.test_memory_activation:_NonJsonPropertyMemory"
            )
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            snapshot = stage.update(
                DecisionFrameContext("frame_9", 9, 900),
                Observation("obs_9", 890, {}),
            )
            self.assertEqual(snapshot.health, "error")
            self.assertIn("JSON", snapshot.error or "")

    def test_activation_document_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "memory activation is missing"):
                read_memory_activation(Path(tmp) / "active.json")

        for payload in ([], "x", None):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                    read_memory_activation(_write_payload(tmp, payload))

        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["schema"] = "old"
            with self.assertRaisesRegex(ValueError, "unsupported schema"):
                read_memory_activation(_write_payload(tmp, payload))

        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"] = None
            with self.assertRaisesRegex(ValueError, "no memory section"):
                read_memory_activation(_write_payload(tmp, payload))

        for field in ("implementation_id", "implementation_spec"):
            for value in ("", "  ", None, 7):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                    payload = _valid_payload()
                    payload["memory"][field] = value
                    with self.assertRaisesRegex(ValueError, f"no {field}"):
                        read_memory_activation(_write_payload(tmp, payload))

        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"] = []
            with self.assertRaisesRegex(ValueError, "invalid implementation_config"):
                read_memory_activation(_write_payload(tmp, payload))

    def test_implementation_id_mismatch_is_rejected_at_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_id"] = "other_id"
            with self.assertRaisesRegex(ValueError, "implementation_id mismatch"):
                ActivatedMemoryStage(
                    read_memory_activation(_write_payload(tmp, payload))
                )

    def test_selected_config_is_detached_from_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            activation = read_memory_activation(_write_payload(tmp, payload))
            activation.implementation_config["max_records"] = 99
            self.assertEqual(activation.payload["memory"]["implementation_config"]["max_records"], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
