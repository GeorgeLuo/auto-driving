"""Later steps read the current observation and evidence from the same map."""
import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.observation.publication import OBSERVATION_KEY, read_observation
from autonomy.decision_cycle.observation.runner import ObservationRunner
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from autonomy.decision_cycle.proposal.values import ActionProposal
from implementations.decision_cycle.plan.plugins.highest_confidence.plugin import HighestConfidencePlan


class SharedCycleInputsTests(unittest.TestCase):
    def test_observation_memory_proposal_and_plan_share_publications(self):
        observation = Observation("obs", 100, {}, metadata={"ground_truth": "ordinary plugin data"})
        shared = {}
        seen = []

        class Observe:
            plugin_id = "observe"
            def observe(self, context, perception):
                return observation

        class Remember:
            plugin_id = "remember"
            contract = MemoryPluginContract()
            def update(self, context):
                seen.append(read_observation(context.shared_memory))
                context.shared_memory[EVIDENCE_KEY] = ()

        class Propose:
            plugin_id = "propose"
            def propose(self, context):
                seen.append(read_observation(context.shared_memory))
                seen.append(context.shared_memory[EVIDENCE_KEY])
                context.shared_memory["proposal.test"] = observation
                return ActionProposal(plugin_id=self.plugin_id, frame_id=context.frame_id,
                                      lifecycle="inactive", freshness="none", confidence=None,
                                      reason="test", command=None, available=False)

        class Plan(HighestConfidencePlan):
            def plan(self, context, candidates):
                seen.append(read_observation(context.shared_memory))
                seen.append(context.shared_memory[EVIDENCE_KEY])
                seen.append(context.shared_memory["proposal.test"])
                return super().plan(context, candidates)

        result = DecisionCycle(DecisionSteps(
            observation=ObservationRunner.from_plugins({"observe": Observe()}),
            memory=MemoryRunner.from_plugins({"remember": Remember()}),
            proposal=ProposalRunner.from_plugins({"propose": Propose()}),
            plan=PlanRunner.from_plugins({"highest_confidence": Plan()}),
        )).run(DecisionFrameContext("f", 0, 100, shared_memory=shared))
        self.assertEqual(seen, [observation, observation, (), observation, (), observation])
        self.assertIs(shared[OBSERVATION_KEY], observation)
        self.assertNotIn("source", result.proposal.to_dict())
        self.assertEqual(result.plan.timestamp_ms, 100)

    def test_the_cycle_writes_the_observation_for_every_slot(self):
        observation = Observation("obs", 100, {})

        class Observe:
            plugin_id = "observe"
            value = observation
            def observe(self, context, perception):
                return self.value

        plugin = Observe()
        runner = ObservationRunner.from_plugins({"observe": plugin})
        shared = {}
        runner(DecisionFrameContext("runner", 0, 100, shared_memory=shared), None)
        self.assertNotIn(OBSERVATION_KEY, shared)
        for slot in (runner, lambda context, perception: plugin.value):
            with self.subTest(slot=slot):
                plugin.value = observation
                DecisionCycle(DecisionSteps(observation=slot)).run(
                    DecisionFrameContext("first", 0, 100, shared_memory=shared))
                self.assertIs(read_observation(shared), observation)
                plugin.value = None
                DecisionCycle(DecisionSteps(observation=slot)).run(
                    DecisionFrameContext("next", 1, 200, shared_memory=shared))
                self.assertNotIn(OBSERVATION_KEY, shared)
        shared[OBSERVATION_KEY] = observation
        DecisionCycle(DecisionSteps()).run(DecisionFrameContext("none", 2, 300, shared_memory=shared))
        self.assertNotIn(OBSERVATION_KEY, shared)

    def test_unknown_observations_read_none(self):
        for memory in (None, {}, {OBSERVATION_KEY: None}, {OBSERVATION_KEY: "invalid"}):
            self.assertIsNone(read_observation(memory))
