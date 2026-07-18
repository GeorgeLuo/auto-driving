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
        implementation_id: str = "recording_test",
    ) -> None:
        self.implementation_id = implementation_id
        self.bounds = MemoryBounds(
            max_records=max_records,
            max_age_ms=max_age_ms,
            eviction_policy=eviction_policy,
        )
        self.fail_on_update = fail_on_update
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
