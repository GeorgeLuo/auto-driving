"""Relativistic search choices and the proposal-to-action boundary."""

import unittest

from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from implementations.decision_cycle.catalog import selection_activation
from implementations.decision_cycle.memory.plugins.relativistic_trail.plugin import RelativisticTrail
from implementations.decision_cycle.memory.plugins.relativistic_trail.trail import TRAIL_KEY, cell_key
from implementations.decision_cycle.plan.plugins.highest_confidence.plugin import HighestConfidencePlan
from implementations.decision_cycle.proposal.plugins.relativistic_search.plugin import RelativisticSearch


class RelativisticSearchTests(unittest.TestCase):
    def setUp(self):
        self.memory = {}
        self.context = DecisionFrameContext("f-1", 1, 2000, shared_memory=self.memory)
        RelativisticTrail().update(self.context)
        self.trail = self.memory[TRAIL_KEY]
        self.trail["interval_s"] = 0.06
        self.trail["turn_response"] = {
            "kind": "linear", "gain_per_s": 3.4, "max_angle_rad": 0.0, "turn_sign": "normal"
        }

    def propose(self):
        return RelativisticSearch().propose(self.context)

    def test_missing_trail_abstains_and_missing_obstructions_allow_search(self):
        self.assertTrue(self.propose().available)
        self.memory.pop(TRAIL_KEY)
        self.assertEqual(self.propose().lifecycle, "missing_input")

    def test_frontier_prefers_unvisited_space_and_limits_the_applied_step(self):
        for x in (-0.9, -0.45, 0.0):
            for z in (0.45, 0.90, 1.35, 1.8):
                self.trail["visited"][cell_key(x, z)] = {"visits": 5, "weight": 5.0, "last_visited_at_ms": 2000}
        self.trail["applied_steering"] = 0.03
        command = self.propose().command
        self.assertGreater(command.steering, 0.03)
        self.assertLess(command.steering - 0.03, 0.105)

    def test_contact_in_straight_ahead_path_changes_the_command(self):
        clear = self.propose().command
        self.assertEqual(clear.steering, 0.0)
        self.trail["contacts"].append({
            "x_s": 0.22, "z_s": 1.1, "heading_rad": 0.0, "at_ms": 1950, "direction": 1,
        })
        self.assertLess(self.propose().command.steering, 0.0)

    def test_foreground_obstruction_and_combined_polarity(self):
        obstruction = RetainedEvidence(
            "left-boundary", "floor_boundary", "floor edge",
            MemoryOrigin("observed-1", "boundary", "image", 1960, 1960, "floor_plane", "f-0"),
            confidence=0.95, location=ViewLocation("image", "left", (0.1, 0.7, 0.3, 0.95)),
        )
        self.memory[EVIDENCE_KEY] = (obstruction,)
        normal = self.propose()
        self.assertGreater(normal.command.steering, 0.0)
        self.assertEqual(normal.source_refs[-1].id, "left-boundary")

        self.trail["turn_response"].update(gain_per_s=-3.4, turn_sign="inverted")
        inverted_turn = self.propose().command
        self.assertAlmostEqual(inverted_turn.steering, -normal.command.steering)
        self.assertAlmostEqual(inverted_turn.throttle, normal.command.throttle)

        self.trail["throttle_sign"] = "inverted"
        both_inverted = self.propose().command
        self.assertAlmostEqual(both_inverted.steering, normal.command.steering)
        self.assertAlmostEqual(both_inverted.throttle, -normal.command.throttle)

        self.trail["turn_response"] = None
        warming = self.propose().command
        self.assertGreater(warming.steering, 0.0)
        self.assertLess(warming.throttle, 0.0)

        self.trail["turn_response"] = {"kind": "linear", "gain_per_s": -3.4, "max_angle_rad": 0.0,
                                      "turn_sign": "inverted"}
        self.trail["at_ms"] = 3000
        self.assertEqual(self.propose().command.steering, 0.0)

    def test_escape_commands_use_the_directed_travel_and_yaw(self):
        self.trail["throttle_sign"] = "inverted"
        self.trail["turn_response"].update(gain_per_s=-3.4, turn_sign="inverted")
        self.trail["escape"] = {
            "phase": "back_out", "direction": -1, "turn_side": -1, "heading_at_contact": 0.0,
            "phase_started_at_ms": 2000, "backing_s": 0.0, "cause": "contact",
        }
        back = self.propose()
        self.assertEqual(back.reason, "back_out")
        self.assertGreater(back.command.throttle, 0.0)
        self.assertGreater(back.command.steering, 0.0)
        self.trail["escape"].update(phase="turn_away", direction=1)
        turn = self.propose()
        self.assertEqual(turn.reason, "turn_away")
        self.assertLess(turn.command.throttle, 0.0)
        self.assertLess(turn.command.steering, 0.0)

    def test_proposal_runner_highest_confidence_and_selected_action(self):
        runner = ProposalRunner.from_activation(selection_activation("proposal", plugins=["relativistic_search"]))
        result = runner(self.context)
        self.assertEqual(result.status, "ok")
        proposal, = result.candidates
        self.assertEqual(proposal.lifecycle, "fresh")
        self.assertTrue(proposal.available)
        self.assertIsNone(runner.plugin_report()["plugins"][0]["error"])
        plan = HighestConfidencePlan().plan(self.context, result.candidates)
        self.assertEqual(plan.selected_proposal_id, proposal.proposal_id)
        action = SelectedAction().decide(self.context, plan)
        self.assertTrue(action.applied)
        self.assertEqual(action.control.throttle, proposal.command.throttle)

    def test_reversing_during_escape_preserves_the_yaw_direction(self):
        self.trail["applied_direction"] = 1
        self.trail["applied_steering"] = 0.70
        self.trail["escape"] = {
            "phase": "turn_away", "direction": -1, "turn_side": 1, "heading_at_contact": 0.0,
            "phase_started_at_ms": 2000, "backing_s": 0.42, "cause": "contact",
        }
        reverse = self.propose().command
        self.assertLess(reverse.throttle, 0.0)
        self.assertLess(reverse.steering, -0.70)
        self.trail["escape"]["turn_side"] = -1
        self.assertGreater(self.propose().command.steering, 0.0)
