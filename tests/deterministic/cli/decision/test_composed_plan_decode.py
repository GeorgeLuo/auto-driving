"""A published composed plan decodes strictly and aligns with its authority."""

from __future__ import annotations

import unittest
from dataclasses import replace

from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.proposal.inputs import build_decision_data_source
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.vehicle import SensorFrame
from cli.automa_cli.decision import (
    DecisionSurfaceError,
    _require_aggregate_cycle_alignment,
    _strict_decode_plan,
)
from cli.automa_cli.decision_records import DecisionRecords
from tests.deterministic.autonomy.decision_cycle.action_proposal_plan_fixtures import (
    WeightedMeanPlan,
    _active_proposal,
)

FRAME = {"frame_id": "frame_001", "frame_index": 0, "timestamp_ms": 1}


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


def _composed_cycle() -> DecisionRecords:
    left = _active_proposal(plugin_id="left", steering=0.2)
    right = _active_proposal(plugin_id="right", steering=-0.2)
    source = build_decision_data_source(frame_id="frame_001", frame_index=0, timestamp_ms=1)
    proposal = ProposalResult(
        frame_id="frame_001", status="ok", candidates=(left, right), source=source
    )
    plan = PlanRunner.from_plugins(
        {"weighted_mean": WeightedMeanPlan({"left": 0.25, "right": 0.75})}
    )(_context(), proposal)
    action = ActionRunner.from_plugins({"selected": SelectedAction()})(
        _context(), proposal, plan
    )
    return DecisionRecords(proposal=proposal, plan=plan, action=action)


class ComposedPlanDecodeTests(unittest.TestCase):
    def test_a_composed_plan_round_trips(self) -> None:
        plan = _composed_cycle().plan
        self.assertEqual(_strict_decode_plan(plan.to_dict(), field="cycle.plan"), plan)

    def test_a_v0_plan_is_rejected(self) -> None:
        legacy = _composed_cycle().plan.to_dict()
        legacy["schema"] = "action_plan_v0"
        with self.assertRaises(DecisionSurfaceError) as raised:
            _strict_decode_plan(legacy, field="cycle.plan")
        self.assertEqual(raised.exception.error, "latest_frame_invalid")
        self.assertIn("action_plan_v1", str(raised.exception))

    def test_a_junk_command_or_reason_is_rejected_by_field(self) -> None:
        cases = (
            ("command", "left", "cycle.plan.command must be a ProposedVehicleCommand object"),
            ("reason", 7, "reason must be a string"),
        )
        for key, value, message in cases:
            with self.subTest(key=key):
                payload = _composed_cycle().plan.to_dict()
                payload[key] = value
                with self.assertRaises(DecisionSurfaceError) as raised:
                    _strict_decode_plan(payload, field="cycle.plan")
                self.assertIn(message, str(raised.exception))


class ComposedPlanAlignmentTests(unittest.TestCase):
    def test_authority_proposed_is_the_composed_command(self) -> None:
        _require_aggregate_cycle_alignment(FRAME, _composed_cycle())

    def test_authority_proposed_other_than_the_plan_command_is_rejected(self) -> None:
        cycle = _composed_cycle()
        other = ProposedVehicleCommand(steering=0.5, throttle=0.0, gear="hold")
        authority = replace(cycle.authority, proposed=other, proposed_applied=False)
        cycle = replace(cycle, action=replace(cycle.action, authority=authority))
        with self.assertRaises(DecisionSurfaceError) as raised:
            _require_aggregate_cycle_alignment(FRAME, cycle)
        self.assertIn("authority.proposed must equal the plan command.", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
