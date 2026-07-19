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
    ) -> None:
        from autonomy.decision import MemoryBounds, empty_memory_snapshot

        self.implementation_id = implementation_id
        self.bounds = MemoryBounds(
            max_records=max_records,
            max_age_ms=max_age_ms,
            eviction_policy=eviction_policy,
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
