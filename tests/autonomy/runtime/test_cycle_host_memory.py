from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision import (
    DecisionFrameContext,
    DecisionStages,
    load_memory_stage_if_present,
    read_memory_activation,
)
from autonomy.runtime import AutonomyControl, AutonomyManager, AutonomySnapshot
from autonomy.runtime.cycle_host import AutonomyCycleHost


class _PushyEngine:
    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": "pushy-test",
            "engine_spec": "tests:_PushyEngine",
        }

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        self.last_snapshot = snapshot
        return AutonomyControl(
            steering=0.7,
            throttle=0.4,
            confidence=1.0,
            reason="pushy-test-engine",
        )


class _RecordingMemory:
    def __init__(
        self,
        *,
        max_records: int = 4,
        max_age_ms: int | None = 1_000,
        eviction_policy: str = "oldest_first",
        fail_on_update: bool = False,
        implementation_id: str = "recording_test",
        max_property_bytes: int | None = 4_096,
        max_serialized_bytes: int | None = 262_144,
        **_ignored,
    ) -> None:
        from autonomy.decision import MemoryBounds, empty_memory_snapshot

        self.implementation_id = implementation_id
        self.bounds = MemoryBounds(
            max_records=max_records,
            max_age_ms=max_age_ms,
            eviction_policy=eviction_policy,
            max_property_bytes=max_property_bytes,
            max_serialized_bytes=max_serialized_bytes,
        )
        self.fail_on_update = fail_on_update
        self.epoch = 0
        self._snapshot = self.reset()

    def update(self, context, observation):
        from autonomy.decision import (
            MemoryProvenance,
            MemorySnapshot,
            RetainedEvidence,
            empty_memory_snapshot,
        )

        if self.fail_on_update:
            raise RuntimeError("forced-memory-failure")
        if observation is None:
            self._snapshot = empty_memory_snapshot(
                memory_id=f"mem-{context.frame_id}",
                epoch_id=f"epoch-{self.epoch}",
                bounds=self.bounds,
                created_at_ms=context.timestamp_ms,
                implementation_id=self.implementation_id,
            )
            return self._snapshot
        self._snapshot = MemorySnapshot(
            memory_id=f"mem-{context.frame_id}",
            epoch_id=f"epoch-{self.epoch}",
            health="healthy",
            bounds=self.bounds,
            created_at_ms=context.timestamp_ms,
            records=(
                RetainedEvidence(
                    record_id="rec-1",
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
                ),
            ),
            implementation_id=self.implementation_id,
        )
        return self._snapshot

    def reset(self):
        from autonomy.decision import empty_memory_snapshot

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


def _write_activation(root: Path, *, fail_on_update: bool = False) -> Path:
    path = root / "active.json"
    path.write_text(
        json.dumps(
            {
                "schema": "automa_memory_activation_v0",
                "memory": {
                    "implementation_id": "recording_test",
                    "implementation_spec": (
                        "tests.autonomy.runtime.test_cycle_host_memory:_RecordingMemory"
                    ),
                    "implementation_config": {
                        "max_records": 4,
                        "max_age_ms": 1_000,
                        "fail_on_update": fail_on_update,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


class CycleHostMemoryWiringTests(unittest.TestCase):
    def test_engine_cannot_mutate_stage_owned_memory_through_cycle_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = load_memory_stage_if_present(_write_activation(Path(tmp)))
            self.assertIsNotNone(stage)
            manager = AutonomyManager()
            engine = _PushyEngine()
            manager.engine = engine
            from autonomy.decision import Observation

            host = AutonomyCycleHost(
                manager=manager,
                stages=DecisionStages(
                    observe=lambda context, perception: Observation(
                        observation_id="obs-1",
                        created_at_ms=1,
                        sensor_snapshot={},
                        summary=("test",),
                    ),
                    remember=stage,
                ),
            )
            result = host.run(DecisionFrameContext("frame_1", 0, 1_000))
            assert result.memory is not None
            assert stage is not None
            self.assertIsNot(result.memory, stage.last_snapshot)
            # Mutate the cycle result handed to callers/engines.
            result.memory.metadata["engine_mutated"] = True
            if result.memory.records:
                result.memory.records[0].properties["tamper"] = True
            if hasattr(engine, "last_snapshot") and engine.last_snapshot.memory is not None:
                engine.last_snapshot.memory.metadata["via_engine"] = True
            owned = stage.last_snapshot
            assert owned is not None
            self.assertNotIn("engine_mutated", owned.metadata)
            self.assertNotIn("via_engine", owned.metadata)
            if owned.records:
                self.assertNotIn("tamper", owned.records[0].properties)

    def test_host_passes_memory_snapshot_to_engine_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = load_memory_stage_if_present(_write_activation(Path(tmp)))
            self.assertIsNotNone(stage)
            manager = AutonomyManager()
            manager.engine = _PushyEngine()
            host = AutonomyCycleHost(
                manager=manager,
                stages=DecisionStages(remember=stage),
            )
            from autonomy.decision import Observation

            result = host.run(
                DecisionFrameContext(
                    frame_id="frame_1",
                    frame_index=1,
                    timestamp_ms=100,
                )
            )
            # no observe stage -> observation None; memory still runs
            self.assertIsNotNone(result.memory)
            self.assertEqual(result.control.reason, "pushy-test-engine")
            self.assertTrue(result.control.steering > 0.0)
            self.assertIsNotNone(manager.engine.last_snapshot.memory)
            self.assertIs(manager.engine.last_snapshot.memory, result.memory)

            status = host.status()
            self.assertEqual(status["memory"]["implementation_id"], "recording_test")
            self.assertIn(status["memory"]["last_health"], {"empty", "healthy"})
            self.assertIsNotNone(status["last_cycle"])

            from autonomy.decision import Observation

            stage.update(
                DecisionFrameContext("frame_2", 2, 200),
                Observation("obs_2", 190, {}),
            )
            self.assertEqual(stage.snapshot().health, "healthy")

    def test_memory_failure_does_not_alter_engine_control(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = load_memory_stage_if_present(
                _write_activation(Path(tmp), fail_on_update=True)
            )
            manager = AutonomyManager()
            manager.engine = _PushyEngine()
            host = AutonomyCycleHost(
                manager=manager,
                stages=DecisionStages(remember=stage),
            )
            result = host.run(
                DecisionFrameContext(frame_id="frame_x", frame_index=0, timestamp_ms=1)
            )
            self.assertEqual(result.memory.health, "error")
            self.assertEqual(result.control.reason, "pushy-test-engine")
            self.assertEqual(result.control.steering, 0.7)
            self.assertEqual(result.control.throttle, 0.4)
            self.assertIsNotNone(manager.engine.last_snapshot.memory)
            self.assertEqual(manager.engine.last_snapshot.memory.health, "error")

    def test_idle_engine_reports_has_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = load_memory_stage_if_present(_write_activation(Path(tmp)))
            host = AutonomyCycleHost(stages=DecisionStages(remember=stage))
            result = host.run(
                DecisionFrameContext(frame_id="frame_i", frame_index=0, timestamp_ms=1)
            )
            self.assertEqual(result.control.reason, "stable-idle-engine")
            self.assertTrue(result.control.metadata["has_memory"])
            self.assertIsNotNone(result.memory)

    def test_host_reset_memory_clears_records_and_bumps_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = load_memory_stage_if_present(_write_activation(Path(tmp)))
            self.assertIsNotNone(stage)
            host = AutonomyCycleHost(stages=DecisionStages(remember=stage))
            from autonomy.decision import Observation

            stage.update(
                DecisionFrameContext("frame_fill", 1, 100),
                Observation("obs_fill", 90, {}),
            )
            self.assertEqual(stage.snapshot().health, "healthy")
            self.assertEqual(stage.snapshot().record_count, 1)
            prior_epoch = stage.snapshot().epoch_id

            reset_snapshot = host.reset_memory()
            self.assertIsNotNone(reset_snapshot)
            self.assertIn(reset_snapshot.health, {"empty", "unavailable"})
            self.assertEqual(reset_snapshot.record_count, 0)
            self.assertNotEqual(reset_snapshot.epoch_id, prior_epoch)
            self.assertEqual(host.status()["memory"]["last_record_count"], 0)

    def test_host_reset_memory_without_stage_returns_none(self) -> None:
        host = AutonomyCycleHost()
        self.assertIsNone(host.reset_memory())

    def test_missing_memory_activation_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_memory_stage_if_present(Path(tmp) / "active.json"))

    def test_activation_reader_used_by_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_activation(Path(tmp))
            activation = read_memory_activation(path)
            self.assertEqual(activation.implementation_id, "recording_test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
