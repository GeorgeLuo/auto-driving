"""A plan plugin can compose one command from several candidates."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.inputs import build_decision_data_source
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.vehicle import SensorFrame
from cli.automa_cli.decision import _require_aggregate_cycle_alignment, _strict_decode_plan
from cli.automa_cli.decision_records import DecisionRecords
from tests.deterministic.autonomy.decision_cycle.action_proposal_plan_fixtures import (
    _active_proposal,
)


class WeightedMeanPlan:
    plugin_id = "weighted_mean"

    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights

    def plan(self, candidates, source, shared_memory, *, frame_id, timestamp_ms):
        del source, shared_memory
        active = [
            item
            for item in candidates
            if item.lifecycle in {"fresh", "retained"}
            and item.freshness == item.lifecycle
            and item.available
            and item.command is not None
            and item.proposal_id.endswith(f":{frame_id}")
        ]
        total = sum(self.weights[item.plugin_id] for item in active)
        steering = sum(self.weights[item.plugin_id] * item.command.steering for item in active) / total
        throttle = sum(self.weights[item.plugin_id] * item.command.throttle for item in active) / total
        return ActionPlan(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            status="composed",
            candidates=tuple(candidates),
            selector_id=self.plugin_id,
            selected_proposal_id=None,
            contributions=tuple(
                PlanContribution(
                    proposal_id=item.proposal_id,
                    plugin_id=item.plugin_id,
                    weight=self.weights[item.plugin_id],
                    role="mix",
                )
                for item in active
            ),
            command=ProposedVehicleCommand(steering=steering, throttle=throttle, gear="hold"),
            reason="weighted_mean",
            confidence=None,
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
        source = build_decision_data_source(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        published = ProposalResult(
            frame_id="frame_001", status="ok", candidates=(left, right), source=source
        )
        _require_aggregate_cycle_alignment(
            {"frame_id": "frame_001", "frame_index": 0, "timestamp_ms": 1},
            DecisionRecords(proposal=published, plan=plan, action=result),
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

    def test_strict_decode_accepts_composed_and_upgrades_v0(self) -> None:
        left = _active_proposal(plugin_id="left", steering=0.2)
        right = _active_proposal(plugin_id="right", steering=-0.2)
        proposal = ProposalResult(frame_id="frame_001", status="ok", candidates=(left, right))
        plan = PlanRunner.from_plugins(
            {"weighted_mean": WeightedMeanPlan({"left": 0.5, "right": 0.5})}
        )(_context(), proposal)
        decoded = _strict_decode_plan(plan.to_dict(), field="cycle.plan")
        self.assertEqual(decoded, plan)

        legacy = plan.to_dict()
        legacy["schema"] = "action_plan_v0"
        legacy["status"] = "idle"
        legacy["selected_proposal_id"] = None
        legacy["contributions"] = []
        legacy["command"] = None
        for key in ("command", "reason", "confidence"):
            del legacy[key]
        upgraded = _strict_decode_plan(legacy, field="cycle.plan")
        self.assertEqual(upgraded.schema, "action_plan_v1")
        self.assertEqual(upgraded.status, "idle")
        self.assertIsNone(upgraded.command)
        self.assertEqual(upgraded.reason, "")
        self.assertIsNone(upgraded.confidence)


if __name__ == "__main__":
    unittest.main()
