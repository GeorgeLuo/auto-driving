"""Step slots and serialized names of the cycle records.

Perception evidence, the observation record, and retained evidence are
serialized; the host map is not.
"""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.proposal.values import ACTION_PROPOSAL_SCHEMA
from autonomy.decision_cycle.cycle import DecisionCycleResult, DecisionSteps
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.proposal.inputs import DECISION_DATA_SOURCE_SCHEMA, DecisionDataSource
from autonomy.decision_cycle.observation.values import OBSERVATION_SCHEMA
from autonomy.decision_cycle.memory.snapshots.values import MEMORY_SNAPSHOT_SCHEMA
from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract
from autonomy.runtime.engine import AutonomyControl
from implementations.decision_cycle.perception.catalog import DEFAULT_PERCEPTION_ALGORITHM
from implementations.decision_cycle.perception.frame_observation.plugin import (
    FrameObservationPlugin,
)


class CycleVocabularyTests(unittest.TestCase):
    def test_cycle_operations_keep_their_slots(self) -> None:
        self.assertEqual(
            tuple(DecisionSteps.__dataclass_fields__),
            ("perceive", "observe", "remember", "act"),
        )

    def test_memory_required_remains_the_host_map_field(self) -> None:
        payload = PerceptionPluginContract(memory_required=True).to_dict()
        self.assertEqual(
            set(payload),
            {
                "inputs",
                "state_mode",
                "memory_required",
                "state_ownership",
                "description",
                "assumptions",
                "emits",
                "limitations",
                "diagnostic_artifacts",
                "diagnostics_required",
            },
        )
        self.assertTrue(payload["memory_required"])
        self.assertEqual(payload["state_ownership"], "host_shared_memory")
        self.assertFalse(PerceptionPluginContract().to_dict()["memory_required"])

    def test_records_keep_serialized_names_and_leave_the_host_map_out(self) -> None:
        self.assertEqual(OBSERVATION_SCHEMA, "decision_observation_v1")
        self.assertEqual(MEMORY_SNAPSHOT_SCHEMA, "decision_memory_snapshot_v0")
        self.assertEqual(ACTION_PROPOSAL_SCHEMA, "action_proposal_v0")
        self.assertEqual(DECISION_DATA_SOURCE_SCHEMA, "decision_data_source_v0")
        self.assertEqual(DEFAULT_PERCEPTION_ALGORITHM, "lightweight_observer")
        self.assertEqual(FrameObservationPlugin.plugin_id, "frame-observation-v0")

        self.assertIn("memory", DecisionDataSource.__dataclass_fields__)
        self.assertIn("observation", DecisionDataSource.__dataclass_fields__)
        self.assertNotIn("shared_memory", DecisionDataSource.__dataclass_fields__)
        self.assertIn("shared_memory", DecisionFrameContext.__dataclass_fields__)

        result = DecisionCycleResult(
            context=DecisionFrameContext(frame_id="frame", frame_index=0, timestamp_ms=0),
            perception=None,
            observation=None,
            memory=None,
            action=None,
            control=AutonomyControl(),
            started_at_ms=0,
            completed_at_ms=0,
        )
        payload = result.to_dict()
        self.assertEqual(
            set(payload),
            {
                "schema",
                "started_at_ms",
                "completed_at_ms",
                "duration_ms",
                "context",
                "perception",
                "observation",
                "memory",
                "action",
                "control",
            },
        )
        self.assertNotIn("shared_memory", payload["context"])
