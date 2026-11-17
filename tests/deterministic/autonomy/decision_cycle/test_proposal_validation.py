from __future__ import annotations
import unittest
from unittest.mock import patch
from autonomy.decision_cycle.proposal.values import (
    ActionProposal,
)
from autonomy.serialization import canonical_json_size_bytes
from tests.deterministic.autonomy.decision_cycle.action_proposal_plan_fixtures import (
    _active_proposal,
)
from autonomy.decision_cycle.plan.runner import PlanRunner
from tests.support.action_fixtures import DecisionChain, decision_chain, proposal_runner


class RunnerBoundaryTests(unittest.TestCase):
    def test_plan_and_source_empty_metadata_is_object(self) -> None:
        from autonomy.decision_cycle.plan.values import ActionPlan

        plan = ActionPlan(
            frame_id="frame_001",
            timestamp_ms=1,
            status="idle",
            candidates=(_active_proposal(),),
            selector_id="highest_confidence",
            selected_proposal_id=None,
            contributions=(),
        )
        self.assertEqual(plan.to_dict()["metadata"], {})

    def test_plugin_cannot_admit_oversize_via_metadata_mutation(self) -> None:
        from autonomy.serialization import FrozenJsonObject

        def corrupt(context) -> ActionProposal:
            proposal = ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id=context.frame_id,
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
            # Rebinding metadata after construction is re-read at admission.
            # Payload size is not a reason to refuse the candidate.
            object.__setattr__(
                proposal, "metadata", FrozenJsonObject({"pad": "x" * 5000})
            )
            self.assertGreater(canonical_json_size_bytes(proposal.to_dict()), 4096)
            return proposal

        engine = decision_chain(
            plugins={"avoid_recent_obstruction": corrupt},
        )
        result = engine.run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.plan)
        (admitted,) = [
            candidate
            for candidate in result.plan.candidates
            if candidate.plugin_id == "avoid_recent_obstruction"
        ]
        self.assertEqual(admitted.metadata["pad"], "x" * 5000)

    def test_admission_keeps_the_typed_candidate_and_immutable_nested_fields(self) -> None:
        from autonomy.decision_cycle.context import DecisionFrameContext
        from autonomy.decision_cycle.proposal.runner import ProposalRunner

        metadata = {"nested": {"choices": [1, {}]}}
        refs = list(_active_proposal().source_refs)
        assumptions = ["image_relative"]
        candidate = ActionProposal(
            plugin_id="avoid_recent_obstruction", frame_id="frame_001", lifecycle="fresh", freshness="fresh",
            confidence=0.5, reason="drive", command=_active_proposal().command,
            source_refs=refs, assumptions=assumptions, metadata=metadata, available=True,
        )
        class Plugin:
            plugin_id = "avoid_recent_obstruction"

            def propose(self, context):
                return candidate

        runner = ProposalRunner.from_plugins({"avoid_recent_obstruction": Plugin()})
        metadata["nested"]["choices"].append(99)
        refs.clear()
        assumptions.clear()
        with patch.object(ActionProposal, "to_dict", side_effect=AssertionError("admission exported candidate")), \
             patch.object(ActionProposal, "from_dict", side_effect=AssertionError("admission parsed candidate")):
            result = runner(DecisionFrameContext("frame_001", 0, 1))
        self.assertEqual(result.status, "ok")
        self.assertIs(result.candidates[0], candidate)
        self.assertEqual(candidate.metadata["nested"]["choices"], (1, {}))
        self.assertEqual(len(candidate.source_refs), 1)
        self.assertEqual(candidate.assumptions, ("image_relative",))
        with self.assertRaises(TypeError):
            candidate.metadata["nested"]["choices"] = ()

    def test_admission_rejects_corrupted_nested_values(self) -> None:
        from autonomy.serialization import FrozenJsonObject

        for target, field, value in (
            ("command", "steering", True), ("command", "throttle", 2),
            ("source", "frame_id", " padded "), ("source", "kind", "unknown"),
            ("proposal", "metadata", {"mutable": []}),
            ("proposal", "metadata", FrozenJsonObject({"mutable": []})),
        ):
            def corrupt(context):
                candidate = _active_proposal(frame_id=context.frame_id)
                nested = {"proposal": candidate, "command": candidate.command, "source": candidate.source_refs[0]}[target]
                object.__setattr__(nested, field, value)
                return candidate

            with self.subTest(target=target, field=field, value=value):
                result = decision_chain(plugins={"avoid_recent_obstruction": corrupt}).run(
                    frame_id="frame_001", frame_index=0, timestamp_ms=1,
                )
                self.assertEqual((result.status, result.reason), ("error", "action_proposal_matrix_violated"))

    def test_select_action_plan_failure_is_plan_invariant(self) -> None:
        class BrokenPlan:
            plugin_id = "broken"

            def plan(self, context, candidates):
                raise ValueError("plan broken")

        engine = DecisionChain(proposal_runner({}), plan=PlanRunner.from_plugins({"broken": BrokenPlan()}))
        result = engine.run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        control = result.control
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "action_plan_invariant_violated")
        self.assertIsNone(result.plan)
        self.assertEqual(
            result.authority.authorized_output["reason"], "action_plan_invariant_violated"
        )
        self.assertEqual(control.steering, 0.0)

    def test_corrupted_lifecycle_matrix_is_engine_error(self) -> None:
        def corrupt_matrix(context) -> ActionProposal:
            proposal = ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id=context.frame_id,
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
        def bad_available(context) -> ActionProposal:
            proposal = _active_proposal(frame_id=context.frame_id)
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
        def active(context) -> ActionProposal:
            return _active_proposal(frame_id=context.frame_id, steering=0.35)

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
        self.assertIs(result.authority.proposed, selected.command)
        self.assertEqual(
            result.authority.proposed.to_dict(), selected.command.to_dict()
        )
        self.assertEqual(control.steering, 0.35)
        self.assertTrue(result.authority.proposed_applied)
