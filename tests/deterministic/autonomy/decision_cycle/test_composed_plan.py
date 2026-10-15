"""A plan plugin can compose one command from several candidates."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.vehicle import SensorFrame
from tests.deterministic.autonomy.decision_cycle.action_proposal_plan_fixtures import (
    WeightedMeanPlan,
    _active_proposal,
)


def _context() -> DecisionFrameContext:
    return DecisionFrameContext(
        "frame_001",
        0,
        1,
        shared_memory={},
        sensor_frame=SensorFrame(
            read_id="frame_001", readings={}, started_at_ms=1, completed_at_ms=1
        ),
        mode="autonomy",
    )


class ComposedPlanTests(unittest.TestCase):
    def test_weighted_mean_reaches_the_applied_control(self) -> None:
        left = _active_proposal(plugin_id="left", confidence=0.2, steering=0.2)
        right = _active_proposal(plugin_id="right", confidence=0.9, steering=-0.2)
        proposal = ProposalResult(
            frame_id="frame_001", status="ok", candidates=(left, right), source=None
        )
        plan_runner = PlanRunner.from_plugins(
            {"weighted_mean": WeightedMeanPlan({"left": 0.25, "right": 0.75})}
        )
        plan = plan_runner(_context(), proposal)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.status, "composed")
        self.assertIsNone(plan.selected_proposal_id)
        self.assertAlmostEqual(plan.command.steering, -0.1)
        self.assertEqual(plan.command.throttle, 0.0)
        self.assertEqual([item.plugin_id for item in plan.contributions], ["left", "right"])

        action = ActionRunner.from_plugins({"selected": SelectedAction()})
        result = action(_context(), proposal, plan)
        self.assertEqual(result.status, "ok")
        self.assertAlmostEqual(result.authority.proposed.steering, -0.1)
        self.assertAlmostEqual(result.control.steering, -0.1)
        self.assertEqual(result.control.reason, "weighted_mean")
        self.assertIsNone(result.control.confidence)
        self.assertEqual(
            result.control.metadata["proposal_ids"],
            [item.proposal_id for item in plan.contributions],
        )

    def test_selected_command_must_match_the_candidate(self) -> None:
        candidate = _active_proposal()
        with self.assertRaises(ValueError):
            ActionPlan(
                frame_id="frame_001",
                timestamp_ms=1,
                status="selected",
                candidates=(candidate,),
                selector_id="highest_confidence",
                selected_proposal_id=candidate.proposal_id,
                contributions=(
                    PlanContribution(
                        proposal_id=candidate.proposal_id,
                        plugin_id=candidate.plugin_id,
                    ),
                ),
                command=ProposedVehicleCommand(steering=0.9, throttle=0.0, gear="hold"),
                reason=candidate.reason,
                confidence=candidate.confidence,
            )


if __name__ == "__main__":
    unittest.main()
