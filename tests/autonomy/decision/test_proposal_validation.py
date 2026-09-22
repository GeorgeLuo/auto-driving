from __future__ import annotations
import unittest
from autonomy.decision.action_proposal import (
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
)
from autonomy.decision.memory import canonical_json_bytes
from autonomy.decision.shadow_ids import ShadowCycleInputError
from autonomy.decision.shadow_runner import ShadowProposalsConfig, ShadowProposalsEngine
from implementations.decision.catalog import create_shadow_proposals_engine
from tests.autonomy.decision.action_proposal_plan_fixtures import (
    _active_proposal,
)


class RunnerBoundaryTests(unittest.TestCase):
    def test_plan_and_source_empty_metadata_is_object(self) -> None:
        from autonomy.decision.action_plan import ActionPlan
        from autonomy.decision.decision_data import build_decision_data_source

        plan = ActionPlan(
            frame_id="frame_001",
            timestamp_ms=1,
            status="idle",
            candidates=(_active_proposal(),),
            selected_proposal_id=None,
            contributions=(),
        )
        self.assertEqual(plan.to_dict()["metadata"], {})
        source = build_decision_data_source(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(source.to_dict()["metadata"], {})
        # Nested empty object in ready envelope value.
        from autonomy.decision.decision_data import ready_envelope

        env = ready_envelope({"empty": {}, "arr": []}, updated_at_ms=1)
        self.assertEqual(env.to_dict()["value"], {"empty": {}, "arr": []})

    def test_frame_id_65_chars_raises(self) -> None:
        engine = create_shadow_proposals_engine()
        with self.assertRaises(ShadowCycleInputError):
            engine.run_cycle(frame_id="f" * 65, frame_index=0, timestamp_ms=1)

    def test_vehicle_action_not_valid_command(self) -> None:
        from autonomy.vehicle import VehicleAction

        with self.assertRaises((TypeError, ValueError)):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="fresh",
                freshness="fresh",
                confidence=0.5,
                reason="bad",
                command=VehicleAction(steering=0.1),  # type: ignore[arg-type]
                source_refs=(SourceRef(kind="memory_record", id="r"),),
                available=True,
            )

    def test_non_string_json_keys_rejected(self) -> None:
        from autonomy.decision.shadow_ids import deep_freeze

        for bad in ({1: "value"}, {True: "x"}, {None: "y"}, {"ok": {2: "nested"}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    deep_freeze(bad)
                with self.assertRaises(ValueError):
                    ActionProposal(
                        plugin_id="avoid_recent_obstruction",
                        frame_id="frame_001",
                        lifecycle="inactive",
                        freshness="none",
                        confidence=0.0,
                        reason="noop",
                        command=None,
                        available=False,
                        metadata=bad,  # type: ignore[arg-type]
                    )

    def test_plugin_cannot_admit_oversize_via_metadata_mutation(self) -> None:
        from autonomy.decision.decision_data import DecisionDataSource
        from autonomy.decision.shadow_ids import FrozenJsonObject

        def corrupt(source: DecisionDataSource) -> ActionProposal:
            proposal = ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id=source.frame_id,
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="noop",
                command=None,
                available=False,
                metadata={},
            )
            # Sealed storage rejects ordinary growth paths.
            with self.assertRaises(Exception):
                proposal.metadata._data["pad"] = "x" * 5000  # type: ignore[index]
            # Even if a plugin rebinds metadata after construction, admission
            # re-validates bounds and refuses oversized candidates.
            object.__setattr__(
                proposal, "metadata", FrozenJsonObject({"pad": "x" * 5000})
            )
            self.assertGreater(canonical_json_bytes(proposal.to_dict()), 4096)
            return proposal

        engine = ShadowProposalsEngine(
            config=ShadowProposalsConfig(
                enabled_plugins=("avoid_recent_obstruction",),
            ),
            plugins={"avoid_recent_obstruction": corrupt},
        )
        result, control = engine.run_cycle(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(result.status, "engine_error")
        self.assertEqual(result.reason, "action_proposal_matrix_violated")
        self.assertIsNone(result.plan)
        self.assertEqual(
            result.authority.authorized_output["reason"], "shadow-only-idle"
        )
        self.assertEqual(control.steering, 0.0)

    def test_select_action_plan_failure_is_plan_invariant(self) -> None:
        from unittest.mock import patch

        engine = create_shadow_proposals_engine()
        with patch(
            "autonomy.decision.shadow_runner.select_action_plan",
            side_effect=ValueError("plan broken"),
        ):
            result, control = engine.run_cycle(
                frame_id="frame_001", frame_index=0, timestamp_ms=1
            )
        self.assertEqual(result.status, "engine_error")
        self.assertEqual(result.reason, "action_plan_invariant_violated")
        self.assertIsNone(result.plan)
        self.assertEqual(
            result.authority.authorized_output["reason"], "shadow-only-idle"
        )
        self.assertEqual(control.steering, 0.0)

    def test_corrupted_lifecycle_matrix_is_engine_error(self) -> None:
        from autonomy.decision.decision_data import DecisionDataSource

        def corrupt_matrix(source: DecisionDataSource) -> ActionProposal:
            proposal = ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id=source.frame_id,
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="noop",
                command=None,
                available=False,
            )
            # Post-construction constructor-bug simulation.
            object.__setattr__(proposal, "lifecycle", "fresh")
            object.__setattr__(proposal, "freshness", "stale")
            object.__setattr__(proposal, "available", True)
            return proposal

        engine = ShadowProposalsEngine(
            config=ShadowProposalsConfig(
                enabled_plugins=("avoid_recent_obstruction",),
            ),
            plugins={"avoid_recent_obstruction": corrupt_matrix},
        )
        result, _ = engine.run_cycle(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(result.status, "engine_error")
        self.assertEqual(result.reason, "action_proposal_matrix_violated")
        self.assertIsNone(result.plan)

    def test_available_must_be_exact_bool(self) -> None:
        with self.assertRaises(ValueError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="fresh",
                freshness="fresh",
                confidence=0.5,
                reason="x",
                command=ProposedVehicleCommand(steering=0.2, throttle=0.0, gear="hold"),
                source_refs=(SourceRef(kind="memory_record", id="r"),),
                available="false",  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            ActionProposal.from_dict(
                {
                    "plugin_id": "avoid_recent_obstruction",
                    "frame_id": "frame_001",
                    "lifecycle": "fresh",
                    "freshness": "fresh",
                    "confidence": 0.5,
                    "reason": "x",
                    "command": {
                        "schema": "proposed_vehicle_command_v0",
                        "steering": 0.2,
                        "throttle": 0.0,
                        "gear": "hold",
                        "normalized": True,
                    },
                    "assumptions": [],
                    "source_refs": [
                        {
                            "kind": "memory_record",
                            "id": "r",
                            "frame_id": None,
                            "observation_id": None,
                            "plugin_id": None,
                            "note": "",
                        }
                    ],
                    "available": "false",
                    "metadata": {},
                    "proposal_id": "avoid_recent_obstruction:frame_001",
                    "schema": "action_proposal_v0",
                }
            )

    def test_runner_rejects_non_bool_available_after_construction(self) -> None:
        from autonomy.decision.decision_data import DecisionDataSource

        def bad_available(source: DecisionDataSource) -> ActionProposal:
            proposal = _active_proposal(frame_id=source.frame_id)
            object.__setattr__(proposal, "available", "false")  # type: ignore[arg-type]
            return proposal

        engine = ShadowProposalsEngine(
            config=ShadowProposalsConfig(
                enabled_plugins=("avoid_recent_obstruction",),
            ),
            plugins={"avoid_recent_obstruction": bad_available},
        )
        result, control = engine.run_cycle(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(result.status, "engine_error")
        self.assertEqual(result.reason, "action_proposal_matrix_violated")
        self.assertIsNone(result.plan)
        self.assertEqual(control.steering, 0.0)

    def test_metadata_rejects_array_of_pairs(self) -> None:
        with self.assertRaises(TypeError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="noop",
                command=None,
                available=False,
                metadata=[["x", 1]],  # type: ignore[arg-type]
            )
        from autonomy.decision.action_plan import ActionPlan
        from autonomy.decision.decision_data import build_decision_data_source

        with self.assertRaises(TypeError):
            ActionPlan(
                frame_id="frame_001",
                timestamp_ms=1,
                status="idle",
                candidates=(_active_proposal(),),
                selected_proposal_id=None,
                contributions=(),
                metadata=[["x", 1]],  # type: ignore[arg-type]
            )
        with self.assertRaises(TypeError):
            build_decision_data_source(
                frame_id="frame_001",
                frame_index=0,
                timestamp_ms=1,
                metadata=[["x", 1]],  # type: ignore[arg-type]
            )

    def test_assumptions_and_source_refs_require_list_or_tuple(self) -> None:
        with self.assertRaises(TypeError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="none",
                command=None,
                assumptions={"alpha", "beta", "gamma"},  # type: ignore[arg-type]
                available=False,
            )
        with self.assertRaises(TypeError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="none",
                command=None,
                assumptions="shadow_only",  # type: ignore[arg-type]
                available=False,
            )
        with self.assertRaises(TypeError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="none",
                command=None,
                assumptions=(item for item in ("a", "b")),  # type: ignore[arg-type]
                available=False,
            )
        with self.assertRaises(TypeError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="stale",
                freshness="stale",
                confidence=0.0,
                reason="stale",
                command=None,
                source_refs={SourceRef(kind="memory_record", id="r")},  # type: ignore[arg-type]
                available=False,
            )
        with self.assertRaises(TypeError):
            ActionProposal.from_dict(
                {
                    "plugin_id": "avoid_recent_obstruction",
                    "frame_id": "frame_001",
                    "lifecycle": "inactive",
                    "freshness": "none",
                    "confidence": 0.0,
                    "reason": "none",
                    "command": None,
                    "assumptions": {"alpha", "beta"},
                    "source_refs": [],
                    "available": False,
                    "metadata": {},
                    "proposal_id": "avoid_recent_obstruction:frame_001",
                    "schema": "action_proposal_v0",
                }
            )
        with self.assertRaises(TypeError):
            ActionProposal.from_dict(
                {
                    "plugin_id": "avoid_recent_obstruction",
                    "frame_id": "frame_001",
                    "lifecycle": "inactive",
                    "freshness": "none",
                    "confidence": 0.0,
                    "reason": "none",
                    "command": None,
                    "assumptions": [],
                    "source_refs": "not-an-array",
                    "available": False,
                    "metadata": {},
                    "proposal_id": "avoid_recent_obstruction:frame_001",
                    "schema": "action_proposal_v0",
                }
            )

    def test_authority_proposed_is_detached_from_selected_command(self) -> None:
        from autonomy.decision.decision_data import DecisionDataSource

        def active(source: DecisionDataSource) -> ActionProposal:
            return _active_proposal(frame_id=source.frame_id, steering=0.35)

        engine = ShadowProposalsEngine(
            config=ShadowProposalsConfig(
                enabled_plugins=("avoid_recent_obstruction",),
            ),
            plugins={"avoid_recent_obstruction": active},
        )
        result, control = engine.run_cycle(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(result.status, "ok")
        assert result.plan is not None
        selected = result.plan.selected_candidate()
        assert selected is not None
        assert selected.command is not None
        assert result.authority.proposed is not None
        self.assertIsNot(result.authority.proposed, selected.command)
        self.assertEqual(
            result.authority.proposed.to_dict(), selected.command.to_dict()
        )
        self.assertEqual(control.steering, 0.0)
        self.assertFalse(result.authority.proposed_applied)
