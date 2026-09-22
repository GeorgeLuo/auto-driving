from __future__ import annotations
import json
import unittest
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON
from autonomy.runtime.manager import AutonomyManager
from cli.automa_cli.decision import (
    ADAPTER_ENGINE_SPEC,
    DECISION_ENGINES,
    ENGINE_ID,
    get_vehicle_decision_info,
    update_vehicle_decision,
)
from implementations.decision.shadow_adapter import ShadowProposalsAutonomyEngine
from tests.cli.decision.shadow_decision_surfaces_fixtures import (
    ShadowDecisionSurfaceFixture,
)


class ShadowDecisionSurfaceTests(ShadowDecisionSurfaceFixture, unittest.TestCase):
    def test_stage_shadow_proposals_and_info_contract(self) -> None:
        update = self._stage()
        self.assertEqual(update.exit_code, 0, update.message)
        payload = json.loads(update.message)
        self.assertEqual(payload["schema"], "vehicle_decision_update_v0")
        self.assertEqual(payload["engine_id"], ENGINE_ID)
        self.assertEqual(
            payload["manifest"]["decision"]["engine_spec"],
            ADAPTER_ENGINE_SPEC,
        )
        self.assertEqual(
            set(payload["manifest"]["decision"]["engine_config"].keys()),
            {
                "enabled_plugins",
                "accepted_kinds",
                "retained_max_age_ms",
                "steer_magnitude",
            },
        )

        info = get_vehicle_decision_info(
            vehicle_id="chase-sim-chaser", json_output=True
        )
        self.assertEqual(info.exit_code, 0, info.message)
        info_payload = json.loads(info.message)
        self.assertEqual(info_payload["schema"], "vehicle_decision_info_v0")
        self.assertIsNotNone(info_payload["shadow"])
        shadow = info_payload["shadow"]
        self.assertEqual(
            shadow["decision_inputs"],
            [
                "observation",
                "memory",
                "patterns",
                "projections",
                "capabilities",
                "prior_host_applied_command",
            ],
        )
        self.assertEqual(shadow["enabled_plugins"], ["avoid_recent_obstruction"])
        self.assertEqual(shadow["selector_id"], "deterministic_first_active")
        self.assertEqual(shadow["authority"]["proposed_applied"], False)
        self.assertEqual(
            shadow["authority"]["authorized_idle_reason"],
            AUTHORIZED_IDLE_REASON,
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
        self.assertIn(AUTHORIZED_IDLE_REASON, human.message)
        self.assertIn("decision-combined-v0", human.message)

    def test_stage_unknown_engine_and_invalid_config(self) -> None:
        result = update_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            engine_id="ghost",
            json_output=True,
        )
        self.assertEqual(result.exit_code, 2)
        payload = json.loads(result.message)
        self.assertEqual(payload["error"], "unknown_engine")
        self.assertIn("shadow-proposals", payload["message"])

        # Invalid catalog config fails closed before write.
        original = dict(DECISION_ENGINES[ENGINE_ID]["engine_config"])
        try:
            DECISION_ENGINES[ENGINE_ID]["engine_config"] = {
                **original,
                "steer_magnitude": 0.0,
            }
            bad = update_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                engine_id=ENGINE_ID,
                json_output=True,
            )
            self.assertEqual(bad.exit_code, 2)
            self.assertEqual(json.loads(bad.message)["error"], "invalid_engine_config")
            activation = (
                self.runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "decision"
                / "active.json"
            )
            self.assertFalse(activation.exists())
        finally:
            DECISION_ENGINES[ENGINE_ID]["engine_config"] = original

    def test_info_missing_activation(self) -> None:
        result = get_vehicle_decision_info(vehicle_id="missing", json_output=True)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(json.loads(result.message)["error"], "activation_missing")

    def test_staged_adapter_loads_via_autonomy_manager(self) -> None:
        self._stage()
        entry = DECISION_ENGINES[ENGINE_ID]
        manager = AutonomyManager(
            default_engine_spec=entry["engine_spec"],
            default_engine_config=dict(entry["engine_config"]),
        )
        self.assertIsInstance(manager.engine, ShadowProposalsAutonomyEngine)
