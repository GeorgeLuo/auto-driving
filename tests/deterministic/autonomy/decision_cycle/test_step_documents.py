"""Step records reject inconsistent nested data and preserve plugin JSON."""
from __future__ import annotations

import unittest

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.action.values import AuthorityResult
from autonomy.decision_cycle.memory.interface import MemoryPluginReport, MemoryReport
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.fields import FieldError
from tests.deterministic.autonomy.test_documents import _authority, _plan


class StepDocumentTests(unittest.TestCase):
    def test_applied_authority_requires_the_proposed_command_and_derived_flag(self):
        payload = _authority(True).to_dict()
        payload["authorized_output"].update(steering=0.25, throttle=-0.5)
        payload.update(proposed_applied=True, proposed_equals_authorized=True)
        authority = AuthorityResult.from_dict(payload)
        self.assertTrue(authority.proposed_applied)
        payload["proposed_equals_authorized"] = False
        with self.assertRaises(FieldError) as raised:
            AuthorityResult.from_dict(payload)
        self.assertEqual(raised.exception.field_name, "proposed_equals_authorized")
        payload["proposed_equals_authorized"] = True
        payload["authorized_output"]["steering"] = 0
        with self.assertRaises(FieldError):
            AuthorityResult.from_dict(payload)

    def test_action_requires_its_authoritys_frame_and_status(self):
        payload = ActionResult("frame-1", "ok", _authority(True)).to_dict()
        payload["authority"]["frame_id"] = "frame-2"
        with self.assertRaises(FieldError) as raised:
            ActionResult.from_dict(payload)
        self.assertEqual(raised.exception.field_name, "authority.frame_id")

    def test_selected_plan_names_the_changed_command_component(self):
        payload = _plan(True).to_dict()
        payload["command"]["steering"] = -1
        with self.assertRaises(FieldError) as raised:
            ActionPlan.from_dict(payload)
        self.assertEqual(raised.exception.field_name, "command.steering")
        payload = _plan(True).to_dict()
        payload["contributions"][0]["weight"] = True
        with self.assertRaises(FieldError) as raised:
            ActionPlan.from_dict(payload)
        self.assertEqual(raised.exception.field_name, "contributions.0.weight")

    def test_contribution_validates_each_part_of_a_composite_identifier(self):
        plugin_id, frame_id = "p" * 64, "f" * 64
        contribution = PlanContribution(f"{plugin_id}:{frame_id}", plugin_id)
        self.assertEqual(PlanContribution.from_dict(contribution.to_dict()), contribution)
        for proposal_id in (" fixture:frame-1 ", "other:frame-1", "fixture:", "fixture:frame/1"):
            with self.subTest(proposal_id=proposal_id), self.assertRaises(FieldError) as raised:
                PlanContribution(proposal_id, "fixture")
            self.assertEqual(raised.exception.field_name, "proposal_id")

    def test_plugin_state_is_polymorphic_json_and_is_immutable(self):
        for state in (None, "ready", 1, False, [1, {"value": 2}], {"records": [{"value": 1}]}):
            with self.subTest(state=state):
                plugin = MemoryPluginReport.from_dict({"plugin_id": "fixture", "state": state})
                self.assertEqual(plugin.to_dict()["state"], state)
        source = {"records": [{"value": 1}]}
        report = MemoryReport("memory_report", (MemoryPluginReport("fixture", source),))
        source["records"][0]["value"] = 2
        self.assertEqual(report.to_dict()["plugins"][0]["state"]["records"][0]["value"], 1)
        with self.assertRaises(TypeError):
            report.plugins[0].state["records"][0]["value"] = 3
        with self.assertRaises(FieldError) as raised:
            MemoryReport.from_dict({"schema": "memory_report", "plugins": [{"plugin_id": "fixture", "state": float("nan")}],
                                    "evidence_publisher": None, "beliefs": None})
        self.assertEqual(raised.exception.field_name, "plugins.0.state")
