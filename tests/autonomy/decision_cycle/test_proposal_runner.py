from __future__ import annotations

import unittest

from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from autonomy.decision_cycle.proposal.values import ActionProposal
from tests.support.action_fixtures import DecisionChain


class _ConfiguredProposal:
    """Proposes idle under its configured ID and records its construction config."""

    def __init__(self, *, plugin_id: str, reason: str = "idle") -> None:
        self.plugin_id = plugin_id
        self.reason = reason

    def propose(self, source, shared_memory) -> ActionProposal:
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=source.frame_id,
            lifecycle="inactive",
            freshness="none",
            confidence=0.0,
            reason=self.reason,
            command=None,
            available=False,
        )



class _ReadsEvidence(_ConfiguredProposal):
    evidence_key = "test.evidence"


def _spec(name: str) -> str:
    return f"{__name__}:{name}"


def _activation(plugins, count: int = 6):
    ids = [f"p{index}" for index in range(count)]
    return step_activation(
        "proposal",
        plugins,
        {plugin_id: _spec("_ConfiguredProposal") for plugin_id in ids},
        {plugin_id: {"plugin_id": plugin_id, "reason": f"from_{plugin_id}"} for plugin_id in ids},
    )


class ProposalRunnerTests(unittest.TestCase):
    def test_runner_is_scoped_to_the_proposal_step(self) -> None:
        runner = ProposalRunner.from_activation(_activation(["p0"], count=1))
        self.assertEqual(runner.plugin_manager.step, "proposal")
        self.assertEqual(runner.plugin_manager.available_ids, ("p0",))
        self.assertEqual(runner.plugin_ids, ("p0",))

    def test_selection_has_no_count_limit_and_keeps_order(self) -> None:
        order = ["p5", "p0", "p3", "p1", "p4", "p2"]
        runner = ProposalRunner.from_activation(_activation(order))
        self.assertEqual(runner.plugin_ids, tuple(order))
        self.assertEqual(runner.plugins["p3"].reason, "from_p3")

        result = DecisionChain(runner).run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        assert result.plan is not None
        self.assertEqual(
            [candidate.reason for candidate in result.plan.candidates],
            [f"from_p{index}" for index in range(6)],
        )

    def test_empty_selection_proposes_nothing(self) -> None:
        runner = ProposalRunner.from_activation(_activation([]))
        self.assertEqual(runner.plugins, {})
        result = runner.run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.candidates, ())


    def test_declared_evidence_key_is_copied_into_the_source(self) -> None:
        runner = ProposalRunner.from_activation(
            step_activation(
                "proposal",
                ["p0"],
                {"p0": _spec("_ReadsEvidence")},
                {"p0": {"plugin_id": "p0"}},
            )
        )
        self.assertEqual(runner.evidence_key, "test.evidence")
        result = runner.run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1, shared_memory={"test.evidence": ()}
        )
        assert result.source is not None
        self.assertEqual(result.source.evidence.status, "ready")


if __name__ == "__main__":
    unittest.main()
