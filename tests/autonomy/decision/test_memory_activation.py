from __future__ import annotations
import tempfile
import unittest
from autonomy.decision import (
    ActivatedMemoryStage,
    DecisionCycle,
    DecisionFrameContext,
    DecisionStages,
    Observation,
    read_memory_activation,
)
from tests.autonomy.decision.memory_activation_fixtures import (
    _valid_payload,
    _write_payload,
)


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

    def test_update_failures_raise_and_record_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["fail_on_update"] = True
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            with self.assertRaisesRegex(RuntimeError, "forced-update-failure"):
                stage.update(
                    DecisionFrameContext("frame_3", 3, 300),
                    Observation("obs_3", 290, {}),
                )
            self.assertEqual(stage.failure_count, 1)
            self.assertIn("forced-update-failure", stage.last_error or "")

    def test_framework_rejects_over_capacity_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_OverCapacityMemory"
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            with self.assertRaisesRegex(ValueError, "max_records"):
                stage.update(
                    DecisionFrameContext("frame_4", 4, 400),
                    Observation("obs_4", 390, {}),
                )

    def test_framework_rejects_removed_max_age_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_WeakAgeMemory"
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            with self.assertRaisesRegex(ValueError, "max_age_ms"):
                stage.update(
                    DecisionFrameContext("frame_5", 5, 500),
                    Observation("obs_5", 490, {}),
                )

    def test_framework_detaches_returned_snapshots_from_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_MutatingSharedSnapshotMemory"
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
