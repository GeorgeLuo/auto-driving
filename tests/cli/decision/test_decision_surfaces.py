from __future__ import annotations
import json
import unittest
from unittest.mock import patch
from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON
from autonomy.decision_cycle.steps import load_decision_steps
from implementations.decision_cycle.catalog import step_plugins
from cli.automa_cli.decision import get_vehicle_decision_info
from cli.automa_cli.step_activations import update_vehicle_step
from tests.cli.decision.decision_surfaces_fixtures import (
    DecisionSurfaceFixture,
    packaged_decision_steps,
)


class DecisionSurfaceTests(DecisionSurfaceFixture, unittest.TestCase):
    def test_stage_hold_action_and_info_contract(self) -> None:
        identity = self._stage()
        staged = identity["steps"]["proposal"]
        expected = packaged_decision_steps()["proposal"]
        for key in ("plugins", "plugin_specs", "plugin_configs"):
            self.assertEqual(staged[key], expected[key])
        self.assertEqual(staged["metadata"]["vehicle_id"], "chase-sim-chaser")

        info = get_vehicle_decision_info(
            vehicle_id="chase-sim-chaser", json_output=True
        )
        self.assertEqual(info.exit_code, 0, info.message)
        info_payload = json.loads(info.message)
        self.assertEqual(info_payload["schema"], "vehicle_decision_info_v1")
        self.assertEqual(info_payload["generation_id"], identity["generation_id"])
        self.assertEqual(info_payload["steps"]["plan"]["source"], "builtin")
        self.assertEqual(info_payload["steps"]["action"]["plugins"], ["hold"])
        self.assertIsNotNone(info_payload["proposals"])
        proposals = info_payload["proposals"]
        self.assertEqual(
            proposals["decision_inputs"],
            [
                "observation",
                "shared_memory",
                "evidence",
                "capabilities",
                "prior_host_applied_command",
            ],
        )
        self.assertEqual(
            info_payload["proposal_schema_source"]["runner"],
            "autonomy.decision_cycle.proposal.runner:ProposalRunner",
        )
        self.assertEqual(
            info_payload["proposal_schema"]["configuration"]["applied_plugin_ids"],
            proposals["plugins"],
        )
        self.assertEqual(proposals["plugins"], ["avoid_recent_obstruction"])
        self.assertEqual(proposals["selector_id"], "highest_confidence")
        self.assertEqual(proposals["authority"]["proposed_applied"], False)
        self.assertEqual(proposals["authority"]["gate_id"], "hold")
        self.assertEqual(
            proposals["authority"]["authorized_idle_reason"],
            HOLD_IDLE_REASON,
        )
        self.assertEqual(
            info_payload["combined_view"]["view_id"], "decision-combined-v0"
        )
        self.assertIn("path_template", info_payload["combined_view"])

        human = get_vehicle_decision_info(
            vehicle_id="chase-sim-chaser", json_output=False
        )
        self.assertEqual(human.exit_code, 0)
        self.assertIn("avoid_recent_obstruction", human.message)
        self.assertIn(HOLD_IDLE_REASON, human.message)
        self.assertIn("decision-combined-v0", human.message)
        self.assertIn(
            "Schema source: autonomy.decision_cycle.proposal.runner:ProposalRunner.describe_schema()",
            human.message,
        )
        self.assertIn("Failure policy:", human.message)

    def test_stage_unknown_plugin_and_invalid_config(self) -> None:
        code, message = update_vehicle_step(
            vehicle_id="chase-sim-chaser",
            step="action",
            plugins=["ghost"],
            runtime_root=self.runtime_root,
            json_output=True,
        )
        self.assertEqual(code, 2)
        payload = json.loads(message)
        self.assertEqual(payload["error"], "invalid_selection")
        self.assertIn("hold", payload["available_plugins"])

        # Invalid catalog config fails closed before write.
        entry = step_plugins("proposal")["avoid_recent_obstruction"]
        bad_config = {**entry["default_config"], "steer_magnitude": 0.0}
        with patch.dict(entry, {"default_config": bad_config}):
            code, message = update_vehicle_step(
                vehicle_id="chase-sim-chaser",
                step="proposal",
                runtime_root=self.runtime_root,
                json_output=True,
            )
        self.assertEqual(code, 2, message)
        self.assertEqual(json.loads(message)["error"], "invalid_selection")
        activation = (
            self.runtime_root
            / "chase-sim-chaser"
            / "bundle"
            / "runtime"
            / "proposal"
            / "active.json"
        )
        self.assertFalse(activation.exists())

    def test_info_missing_activation(self) -> None:
        result = get_vehicle_decision_info(vehicle_id="missing", json_output=True)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(json.loads(result.message)["error"], "activation_missing")

    def test_staged_steps_load_from_the_runtime_directory(self) -> None:
        self._stage()
        steps = load_decision_steps(
            self.runtime_root / "chase-sim-chaser" / "bundle" / "runtime"
        )
        self.assertEqual(steps.proposal.status()["plugin_ids"], ["avoid_recent_obstruction"])
        self.assertEqual(steps.action.status()["plugin_ids"], ["hold"])
