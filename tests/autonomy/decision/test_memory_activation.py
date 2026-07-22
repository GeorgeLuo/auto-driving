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
        max_property_bytes: int | None = None,
        max_serialized_bytes: int | None = None,
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
