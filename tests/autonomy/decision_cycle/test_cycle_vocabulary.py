"""One name per step, and the serialized names of the cycle records.

A step is a slot in the cycle. Its noun names the ``DecisionSteps`` field,
the ``DecisionCycleResult`` field, the package, the runner's step, and the
activation directory. Perception evidence, the observation record, and
retained evidence are serialized; the host map is not.
"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from autonomy.decision_cycle.proposal.values import ACTION_PROPOSAL_SCHEMA
from autonomy.decision_cycle.activation import STEPS, step_activation_path
from autonomy.decision_cycle.cycle import DecisionCycleResult, DecisionSteps
from autonomy.decision_cycle.steps import STEP_RUNNERS
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.proposal.inputs import DECISION_DATA_SOURCE_SCHEMA, DecisionDataSource
from autonomy.decision_cycle.observation.values import OBSERVATION_SCHEMA
from autonomy.decision_cycle.memory.interface import MEMORY_REPORT_SCHEMA
from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract
from autonomy.runtime.control import AutonomyControl
from implementations.decision_cycle.perception.presets import DEFAULT_PERCEPTION_PRESET
from implementations.decision_cycle.perception.plugins.frame.plugin import (
    FrameObservationPlugin,
)


class CycleVocabularyTests(unittest.TestCase):
    def test_each_step_has_one_name_everywhere(self) -> None:
        self.assertEqual(
            STEPS,
            ("perception", "observation", "memory", "proposal", "plan", "action"),
        )
        self.assertEqual(tuple(DecisionSteps.__dataclass_fields__), STEPS)
        record_fields = tuple(
            name
            for name in DecisionCycleResult.__dataclass_fields__
            if name in STEPS
        )
        self.assertEqual(record_fields, STEPS)
        self.assertEqual(tuple(STEP_RUNNERS), STEPS)
        for step in STEPS:
            with self.subTest(step=step):
                self.assertEqual(STEP_RUNNERS[step].step, step)
                package = importlib.import_module(f"autonomy.decision_cycle.{step}")
                self.assertEqual(Path(package.__file__).parent.name, step)
                importlib.import_module(f"autonomy.decision_cycle.{step}.runner")
                self.assertEqual(
                    step_activation_path(Path("runtime"), step),
                    Path("runtime") / step / "active.json",
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
        self.assertEqual(MEMORY_REPORT_SCHEMA, "memory_report_v1")
        self.assertEqual(ACTION_PROPOSAL_SCHEMA, "action_proposal_v0")
        self.assertEqual(DECISION_DATA_SOURCE_SCHEMA, "decision_data_source_v1")
        self.assertEqual(DEFAULT_PERCEPTION_PRESET, "lightweight_observer")
        self.assertEqual(FrameObservationPlugin.plugin_id, "frame")

        self.assertIn("evidence", DecisionDataSource.__dataclass_fields__)
        self.assertIn("observation", DecisionDataSource.__dataclass_fields__)
        self.assertNotIn("shared_memory", DecisionDataSource.__dataclass_fields__)
        self.assertIn("shared_memory", DecisionFrameContext.__dataclass_fields__)

        result = DecisionCycleResult(
            context=DecisionFrameContext(frame_id="frame", frame_index=0, timestamp_ms=0),
            perception=None,
            observation=None,
            memory=None,
            proposal=None,
            plan=None,
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
                "proposal",
                "plan",
                "action",
                "control",
            },
        )
        self.assertNotIn("shared_memory", payload["context"])
