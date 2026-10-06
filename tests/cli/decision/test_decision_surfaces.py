from __future__ import annotations
import json
import unittest
from unittest.mock import patch
from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON
from autonomy.decision_cycle.steps import load_decision_steps
from implementations.decision_cycle.catalog import step_plugins
from cli.automa_cli.decision import get_vehicle_proposal_info
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

        info = get_vehicle_proposal_info(
            vehicle_id="chase-sim-chaser", json_output=True
        )
        self.assertEqual(info.exit_code, 0, info.message)
        info_payload = json.loads(info.message)
        self.assertEqual(info_payload["schema"], "vehicle_proposal_info_v1")
        schema = info_payload["proposal_schema"]
        self.assertEqual(
            [item["name"] for item in schema["inputs"]],
            [
                "observation",
                "shared_memory",
                "evidence",
                "capabilities",
                "prior_host_applied_command",
            ],
        )
        self.assertEqual(
            schema["configuration"]["applied_plugin_ids"], ["avoid_recent_obstruction"]
        )
        decision = info_payload["decision"]
        self.assertEqual(decision["generation_id"], identity["generation_id"])
        self.assertEqual(decision["selector_id"], "highest_confidence")
        self.assertEqual(
            decision["authority"],
            {
                "gate_id": "hold",
                "proposed_applied": False,
                "authorized_idle_reason": HOLD_IDLE_REASON,
            },
        )
        self.assertEqual(info_payload["published_view"]["view_id"], "decision-combined-v0")
        self.assertFalse(info_payload["published_view"]["available"])

        human = get_vehicle_proposal_info(vehicle_id="chase-sim-chaser").message
        for expected in (
            "avoid_recent_obstruction",
            HOLD_IDLE_REASON,
            "Decision view: unavailable",
            "vehicles decision inspect",
            "Failure policy:",
        ):
            self.assertIn(expected, human)

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
        result = get_vehicle_proposal_info(vehicle_id="missing", json_output=True)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("No active proposal activation found", result.message)

    def test_staged_steps_load_from_the_runtime_directory(self) -> None:
        self._stage()
        steps = load_decision_steps(
            self.runtime_root / "chase-sim-chaser" / "bundle" / "runtime"
        )
        self.assertEqual(steps.proposal.status()["plugin_ids"], ["avoid_recent_obstruction"])
        self.assertEqual(steps.action.status()["plugin_ids"], ["hold"])
