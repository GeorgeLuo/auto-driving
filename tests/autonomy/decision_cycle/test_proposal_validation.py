from __future__ import annotations
import unittest
from autonomy.decision_cycle.proposal.values import (
    ActionProposal,
)
from autonomy.serialization import canonical_json_bytes
from tests.autonomy.decision_cycle.action_proposal_plan_fixtures import (
    _active_proposal,
)
from tests.support.action_fixtures import decision_chain, packaged_decision_chain


class RunnerBoundaryTests(unittest.TestCase):
    def test_plan_and_source_empty_metadata_is_object(self) -> None:
        from autonomy.decision_cycle.plan.values import ActionPlan
        from autonomy.decision_cycle.proposal.inputs import (
            build_decision_data_source,
            ready_envelope,
        )

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

        env = ready_envelope({"empty": {}, "arr": []}, updated_at_ms=1)
        self.assertEqual(env.to_dict()["value"], {"empty": {}, "arr": []})


    def test_plugin_cannot_admit_oversize_via_metadata_mutation(self) -> None:
        from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
        from autonomy.serialization import FrozenJsonObject

        def corrupt(source: DecisionDataSource, shared_memory) -> ActionProposal:
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

        engine = decision_chain(
            plugins={"avoid_recent_obstruction": corrupt},
        )
        result = engine.run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        control = result.control
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "action_proposal_matrix_violated")
        self.assertIsNone(result.plan)
        self.assertEqual(
            result.authority.authorized_output["reason"], "action_proposal_matrix_violated"
        )
        self.assertEqual(control.steering, 0.0)

    def test_select_action_plan_failure_is_plan_invariant(self) -> None:
        from unittest.mock import patch

        engine = packaged_decision_chain()
        with patch(
            "autonomy.decision_cycle.plan.highest_confidence.select_highest_confidence_plan",
            side_effect=ValueError("plan broken"),
        ):
            result = engine.run(
                frame_id="frame_001", frame_index=0, timestamp_ms=1
            )
            control = result.control
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "action_plan_invariant_violated")
        self.assertIsNone(result.plan)
        self.assertEqual(
            result.authority.authorized_output["reason"], "action_plan_invariant_violated"
        )
        self.assertEqual(control.steering, 0.0)

    def test_corrupted_lifecycle_matrix_is_engine_error(self) -> None:
        from autonomy.decision_cycle.proposal.inputs import DecisionDataSource

        def corrupt_matrix(source: DecisionDataSource, shared_memory) -> ActionProposal:
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

        engine = decision_chain(
            plugins={"avoid_recent_obstruction": corrupt_matrix},
        )
        result = engine.run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "action_proposal_matrix_violated")
        self.assertIsNone(result.plan)


    def test_runner_rejects_non_bool_available_after_construction(self) -> None:
        from autonomy.decision_cycle.proposal.inputs import DecisionDataSource

        def bad_available(source: DecisionDataSource, shared_memory) -> ActionProposal:
            proposal = _active_proposal(frame_id=source.frame_id)
            object.__setattr__(proposal, "available", "false")  # type: ignore[arg-type]
            return proposal

        engine = decision_chain(
            plugins={"avoid_recent_obstruction": bad_available},
        )
        result = engine.run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        control = result.control
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "action_proposal_matrix_violated")
        self.assertIsNone(result.plan)
        self.assertEqual(control.steering, 0.0)


    def test_authority_proposed_is_detached_from_selected_command(self) -> None:
        from autonomy.decision_cycle.proposal.inputs import DecisionDataSource

        def active(source: DecisionDataSource, shared_memory) -> ActionProposal:
            return _active_proposal(frame_id=source.frame_id, steering=0.35)

        engine = decision_chain(
            plugins={"avoid_recent_obstruction": active},
        )
        result = engine.run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        control = result.control
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
        self.assertEqual(control.steering, 0.35)
        self.assertTrue(result.authority.proposed_applied)
