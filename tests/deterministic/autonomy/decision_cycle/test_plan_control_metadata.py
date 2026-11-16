"""Applied control identity must come from the actual plan and survive recording."""

import json
import unittest

from autonomy.decision_cycle.action.selected import SelectedAction
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.runtime.execution import ControlApplication
from tests.deterministic.autonomy.decision_cycle.action_proposal_plan_fixtures import _active_proposal


class PlanControlMetadataTests(unittest.TestCase):
    def test_selected_control_carries_proposer_selector_and_plain_plan_metadata(self):
        candidate = _active_proposal()
        plan = ActionPlan(
            frame_id=candidate.frame_id, timestamp_ms=10, status="selected", candidates=(candidate,),
            selector_id="follow_policy", selected_proposal_id=candidate.proposal_id,
            contributions=(PlanContribution(candidate.proposal_id, candidate.plugin_id),),
            command=candidate.command, reason=candidate.reason, confidence=candidate.confidence,
            metadata={"policy": {"name": "get free", "epoch": 4}, "nested": [{"options": []}]},
        )
        context = DecisionFrameContext(candidate.frame_id, 0, 10)
        control = SelectedAction().decide(context, plan).control
        self.assertEqual(control.metadata, {
            "proposal_ids": [candidate.proposal_id], "proposal_id": candidate.proposal_id,
            "plugin_ids": [candidate.plugin_id], "plugin_id": candidate.plugin_id,
            "selector_id": "follow_policy", "plan_metadata": plan.to_dict()["metadata"],
        })
        encoded = json.loads(json.dumps(control.metadata))
        self.assertEqual(encoded, control.metadata)
        application = ControlApplication(candidate.frame_id, "autonomy", True, control.reason, control, 11)
        restored = ControlApplication.from_dict(json.loads(json.dumps(application.to_dict())))
        self.assertEqual(restored.control.metadata, control.metadata)
        control.metadata["plan_metadata"]["nested"][0]["options"].append(1)
        self.assertEqual(plan.to_dict()["metadata"]["nested"], [{"options": []}])
        self.assertEqual(restored.control.metadata["plan_metadata"]["nested"], [{"options": []}])

    def test_composed_identity_uses_contribution_order_without_a_singular_proposer(self):
        left = _active_proposal(plugin_id="left")
        right = _active_proposal(plugin_id="right")
        plan = ActionPlan(
            frame_id=left.frame_id, timestamp_ms=10, status="composed", candidates=(left, right),
            selector_id="mixer", command=left.command, reason="blend", metadata={"blend": [1, 2]},
            contributions=(PlanContribution(right.proposal_id, right.plugin_id, role="mix"),
                           PlanContribution(left.proposal_id, left.plugin_id, role="mix")),
        )
        control = SelectedAction().decide(DecisionFrameContext(left.frame_id, 0, 10), plan).control
        self.assertEqual(control.metadata["plugin_ids"], ["right", "left"])
        self.assertEqual(control.metadata["proposal_ids"], [right.proposal_id, left.proposal_id])
        self.assertNotIn("plugin_id", control.metadata)
        self.assertNotIn("proposal_id", control.metadata)
        self.assertEqual(control.metadata["plan_metadata"], {"blend": [1, 2]})
