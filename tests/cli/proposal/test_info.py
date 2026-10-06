from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.action.hold import HOLD_IDLE_REASON
from autonomy.decision_cycle.activation import (
    read_step_activation,
    step_activation,
    write_step_activation,
)
from autonomy.decision_cycle.steps import load_decision_steps
from implementations.decision_cycle.catalog import step_plugins
from cli.automa_cli.proposal import get_vehicle_proposal_info
from cli.automa_cli.decision_records import DecisionRunners
from cli.automa_cli.step_activations import (
    bundle_activation_path,
    update_vehicle_step,
    vehicle_bundle,
)
from tests.cli.decision.decision_surfaces_fixtures import (
    DecisionSurfaceFixture,
    packaged_decision_steps,
)


class ProposalInfoTests(DecisionSurfaceFixture, unittest.TestCase):
    def test_info_and_replay_load_a_plugin_only_available_in_the_staged_bundle(self) -> None:
        self._stage()
        bundle = vehicle_bundle("chase-sim-chaser", self.runtime_root)
        activation_path = bundle_activation_path(bundle, "proposal")
        original = read_step_activation(activation_path, "proposal")
        plugin_path = Path(bundle["root_dir"]) / (
            "implementations/decision_cycle/proposal/plugins/bundle_only.py"
        )
        plugin_path.write_text(
            "from implementations.decision_cycle.proposal.plugins."
            "avoid_recent_obstruction.plugin import AvoidRecentObstruction\n"
            "class BundleOnlyProposal(AvoidRecentObstruction):\n"
            "    plugin_id = 'bundle_only'\n"
            "    def propose(self, source, shared_memory):\n"
            "        from implementations.decision_cycle.proposal.plugins."
            "bundle_only_helper import MARKER\n"
            "        shared_memory['bundle_marker'] = MARKER\n"
            "        return super().propose(source, shared_memory)\n",
            encoding="utf-8",
        )
        plugin_path.with_name("bundle_only_helper.py").write_text(
            "MARKER = 'from the staged bundle'\n", encoding="utf-8",
        )
        write_step_activation(activation_path, step_activation(
            "proposal", ["bundle_only"],
            {"bundle_only": "implementations.decision_cycle.proposal.plugins.bundle_only:BundleOnlyProposal"},
            metadata=original.metadata,
        ))

        info = get_vehicle_proposal_info(
            vehicle_id="chase-sim-chaser", json_output=True, include_live=False,
        )
        self.assertEqual(info.exit_code, 0, info.message)
        payload = json.loads(info.message)
        self.assertEqual(payload["activation"]["plugins"], ["bundle_only"])
        self.assertEqual(
            payload["proposal_schema"]["configuration"]["applied_plugin_ids"], ["bundle_only"],
        )
        shared_memory = {}
        replay = DecisionRunners.from_payloads(self._identity()["steps"]).run(
            frame_id="bundle-frame", frame_index=1, timestamp_ms=1_000,
            shared_memory=shared_memory,
        )
        self.assertEqual([item.plugin_id for item in replay.proposal.candidates], ["bundle_only"])
        self.assertEqual(shared_memory["bundle_marker"], "from the staged bundle")
        # The generic loader must preserve each slot's step validation.
        wrong_step = read_step_activation(bundle_activation_path(bundle, "action"), "action")
        with self.assertRaisesRegex(ValueError, "activation is for step 'action', not 'proposal'"):
            DecisionRunners.from_activations({"proposal": wrong_step})

    def test_info_reports_a_missing_action_bundle_as_an_activation_error(self) -> None:
        self._stage()
        bundle = vehicle_bundle("chase-sim-chaser", self.runtime_root)
        path = bundle_activation_path(bundle, "action")
        original = read_step_activation(path, "action")
        payload = original.to_payload()
        payload["metadata"]["controller_bundle"]["root_dir"] = str(self.runtime_root / "missing-bundle")
        path.write_text(json.dumps(payload), encoding="utf-8")

        info = get_vehicle_proposal_info(
            vehicle_id="chase-sim-chaser", json_output=True, include_live=False,
        )
        self.assertEqual(info.exit_code, 2)
        self.assertIn("Controller bundle is missing", info.message)

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
