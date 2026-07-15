from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from autonomy.decision import (
    DECISION_CYCLE_RESULT_SCHEMA,
    DecisionCycle,
    DecisionFrameContext,
    DecisionStages,
    Observation,
)
from autonomy.perception import PERCEPTION_TEXT_SCHEMA, PerceptionText
from autonomy.runtime import AutonomyControl


class DecisionStageFlowTests(unittest.TestCase):
    def test_complete_cycle_runs_stages_in_order_and_passes_results_forward(self) -> None:
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
            sensor_snapshot={},
            perception_schema=perception.schema,
            perception_plugin_id=perception.plugin_id,
            summary=perception.lines,
        )
        memory = {"observations": [observation.observation_id]}
        patterns = {"path": "clear"}
        projections = ("continue_forward",)
        control = AutonomyControl(
            steering=0.1,
            throttle=0.2,
            confidence=0.9,
            reason="path-clear",
        )
        stage_calls: list[tuple[str, tuple[int, ...]]] = []

        def record(stage: str, *values: object) -> None:
            stage_calls.append((stage, tuple(id(value) for value in values)))

        def perceive(received_context):
            record("perceive", received_context)
            return perception

        def observe(received_context, received_perception):
            record("observe", received_context, received_perception)
            return observation

        def remember(received_context, received_observation):
            record("remember", received_context, received_observation)
            return memory

        def update_patterns(received_context, received_observation, received_memory):
            record(
                "update_patterns",
                received_context,
                received_observation,
                received_memory,
            )
            return patterns

        def update_projections(
            received_context,
            received_observation,
            received_memory,
            received_patterns,
        ):
            record(
                "update_projections",
                received_context,
                received_observation,
                received_memory,
                received_patterns,
            )
            return projections

        def choose_action(
            received_context,
            received_perception,
            received_observation,
            received_memory,
            received_patterns,
            received_projections,
        ):
            record(
                "choose_action",
                received_context,
                received_perception,
                received_observation,
                received_memory,
                received_patterns,
                received_projections,
            )
            return control

        cycle = DecisionCycle(
            DecisionStages(
                perceive=perceive,
                observe=observe,
                remember=remember,
                update_patterns=update_patterns,
                update_projections=update_projections,
                choose_action=choose_action,
            )
        )

        with patch(
            "autonomy.decision.cycle.timestamp_ms",
            side_effect=(1_000, 1_007),
        ):
            result = cycle.run(context)

        self.assertEqual(
            stage_calls,
            [
                ("perceive", (id(context),)),
                ("observe", (id(context), id(perception))),
                ("remember", (id(context), id(observation))),
                ("update_patterns", (id(context), id(observation), id(memory))),
                (
                    "update_projections",
                    (id(context), id(observation), id(memory), id(patterns)),
                ),
                (
                    "choose_action",
                    (
                        id(context),
                        id(perception),
                        id(observation),
                        id(memory),
                        id(patterns),
                        id(projections),
                    ),
                ),
            ],
        )
        self.assertIs(result.context, context)
        self.assertIs(result.perception, perception)
        self.assertIs(result.observation, observation)
        self.assertIs(result.memory, memory)
        self.assertIs(result.patterns, patterns)
        self.assertIs(result.projections, projections)
        self.assertIs(result.control, control)
        self.assertEqual(result.duration_ms, 7)

        serialized = result.to_dict()
        self.assertEqual(serialized["schema"], DECISION_CYCLE_RESULT_SCHEMA)
        self.assertEqual(serialized["context"]["frame_id"], "frame_007")
        self.assertEqual(serialized["memory"], memory)
        self.assertEqual(serialized["patterns"], patterns)
        self.assertEqual(serialized["projections"], ["continue_forward"])
        self.assertEqual(serialized["control"], control.to_dict())
        json.dumps(serialized)

        serialized["context"]["metadata"]["route"]["candidate"] = "left"
        serialized["memory"]["observations"].append("frame_008")
        self.assertEqual(context.metadata["route"]["candidate"], "center")
        self.assertEqual(memory["observations"], ["frame_007"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
