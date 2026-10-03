from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from autonomy.decision_cycle.cycle import (
    DECISION_CYCLE_RESULT_SCHEMA,
    DecisionCycle,
    DecisionSteps,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
)
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.runtime.control import AutonomyControl
from tests.support.action_fixtures import fixed_action_runner, plan_runner, proposal_runner


class DecisionStageFlowTests(unittest.TestCase):
    def test_complete_cycle_runs_steps_in_order_and_passes_results_forward(self) -> None:
        context = DecisionFrameContext(
            frame_id="frame_007",
            frame_index=7,
            timestamp_ms=700,
            metadata={"route": {"candidate": "center"}},
        )
        perception = PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test-perception",
            status="ok",
            lines=("signal id=path_clear value=true",),
            signals=(),
            things=(),
            limits=("test evidence only",),
        )
        observation = Observation(
            observation_id=context.frame_id,
            created_at_ms=701,
            sensor_frame={},
            perception_schema=perception.schema,
            perception_plugin_id=perception.plugin_id,
            summary=perception.lines,
        )
        shared_memory: dict = {}
        context = DecisionFrameContext(
            frame_id=context.frame_id,
            frame_index=context.frame_index,
            timestamp_ms=context.timestamp_ms,
            metadata=context.metadata,
            shared_memory=shared_memory,
        )
        records = (
                RetainedEvidence(
                    record_id="retained_path_clear",
                    kind="signal",
                    label="path clear evidence",
                    confidence=0.9,
                    origin=MemoryOrigin(
                        observation_id=observation.observation_id,
                        observed_id="path_clear",
                        coordinate_frame="image",
                        observed_at_ms=701,
                        updated_at_ms=702,
                        source_plugin_id=perception.plugin_id,
                        frame_id=context.frame_id,
                    ),
                    location=ViewLocation(frame="image", zone="center"),
                ),
        )
        memory = {
            "schema": "memory_report_v0",
            "plugins": [
                {
                    "plugin_id": "test_memory",
                    "state": {"records": [record.to_dict() for record in records]},
                }
            ],
        }
        control = AutonomyControl(
            steering=0.1,
            throttle=0.2,
            confidence=0.9,
            reason="path-clear",
        )
        proposal_step = proposal_runner({})
        plan_step = plan_runner()
        action_step = fixed_action_runner(control)
        step_calls: list[tuple[str, tuple[int, ...]]] = []

        def record(step: str, *values: object) -> None:
            step_calls.append((step, tuple(id(value) for value in values)))

        def perceive(received_context):
            record("perceive", received_context)
            return perception

        def observe(received_context, received_perception):
            record("observe", received_context, received_perception)
            return observation

        def remember(received_context, received_observation):
            record("remember", received_context, received_observation)
            received_context.shared_memory["test_memory.records"] = records
            return memory

        def propose(received_context, received_observation):
            record("propose", received_context, received_observation)
            # Proposals read what memory published in the host map.
            self.assertIs(received_context.shared_memory["test_memory.records"], records)
            return proposal_step(received_context, received_observation)

        def plan(received_context, received_proposal):
            record("plan", received_context, received_proposal)
            return plan_step(received_context, received_proposal)

        def act(received_context, received_proposal, received_plan):
            record("act", received_context, received_proposal, received_plan)
            return action_step(received_context, received_proposal, received_plan)

        cycle = DecisionCycle(
            DecisionSteps(
                perception=perceive,
                observation=observe,
                memory=remember,
                proposal=propose,
                plan=plan,
                action=act,
            )
        )

        with patch(
            "autonomy.decision_cycle.cycle.timestamp_ms",
            side_effect=(1_000, 1_007),
        ):
            result = cycle.run(context)

        self.assertEqual(
            step_calls,
            [
                ("perceive", (id(context),)),
                ("observe", (id(context), id(perception))),
                ("remember", (id(context), id(observation))),
                ("propose", (id(context), id(observation))),
                ("plan", (id(context), id(result.proposal))),
                ("act", (id(context), id(result.proposal), id(result.plan))),
            ],
        )
        self.assertIs(result.context, context)
        self.assertIs(result.perception, perception)
        self.assertIs(result.observation, observation)
        self.assertIs(result.memory, memory)
        self.assertIs(result.control, control)
        self.assertEqual(result.duration_ms, 7)

        serialized = result.to_dict()
        self.assertEqual(serialized["schema"], DECISION_CYCLE_RESULT_SCHEMA)
        self.assertEqual(serialized["context"]["frame_id"], "frame_007")
        self.assertEqual(serialized["memory"], memory)
        self.assertEqual(serialized["control"], control.to_dict())
        self.assertEqual(serialized["proposal"], result.proposal.to_dict())
        self.assertEqual(serialized["plan"], result.plan.to_dict())
        self.assertEqual(serialized["action"], result.action.to_dict())
        self.assertEqual(serialized["action"]["frame_id"], "frame_007")
        self.assertNotIn("patterns", serialized)
        self.assertNotIn("projections", serialized)
        json.dumps(serialized)

        serialized["context"]["metadata"]["route"]["candidate"] = "left"
        serialized["memory"]["plugins"][0]["state"]["records"][0]["properties"]["mutated"] = True
        self.assertEqual(context.metadata["route"]["candidate"], "center")
        self.assertNotIn("mutated", memory["plugins"][0]["state"]["records"][0]["properties"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
