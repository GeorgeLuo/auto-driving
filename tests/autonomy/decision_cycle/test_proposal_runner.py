from __future__ import annotations

import unittest

from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.proposal.interface import FAILURE_POLICY, PROPOSAL_SCHEMA
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


class _Raises(_ConfiguredProposal):
    def propose(self, source, shared_memory) -> ActionProposal:
        raise RuntimeError("propose failed")


class _RecordsObservation(_ConfiguredProposal):
    seen: list[str] = []

    def propose(self, source, shared_memory) -> ActionProposal:
        self.seen.append(source.observation.status)
        return super().propose(source, shared_memory)


class _ResetRaises(_ConfiguredProposal):
    def reset(self, shared_memory) -> None:
        raise RuntimeError("reset failed")


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

    def test_schema_describes_the_applied_plugins_and_declared_policy(self) -> None:
        runner = ProposalRunner.from_activation(
            step_activation(
                "proposal",
                ["p1", "p0"],
                {"p0": _spec("_ReadsEvidence"), "p1": _spec("_ConfiguredProposal")},
                {"p0": {"plugin_id": "p0"}, "p1": {"plugin_id": "p1"}},
            )
        )
        schema = runner.describe_schema()

        self.assertEqual(schema["schema"], PROPOSAL_SCHEMA)
        self.assertEqual(schema["configuration"]["applied_plugin_ids"], ["p1", "p0"])
        self.assertEqual(
            [(plugin["plugin_id"], plugin["evidence_key"]) for plugin in schema["plugins"]],
            [("p1", None), ("p0", "test.evidence")],
        )
        self.assertEqual(schema["failure_policy"], FAILURE_POLICY.to_dict())

    def test_runner_behaves_as_its_failure_policy_declares(self) -> None:
        def runner(spec: str) -> ProposalRunner:
            return ProposalRunner.from_activation(
                step_activation("proposal", ["p0"], {"p0": _spec(spec)}, {"p0": {"plugin_id": "p0"}})
            )

        # update: isolate_plugin. The raising plugin's candidate is a synthetic error.
        self.assertEqual(FAILURE_POLICY.update, "isolate_plugin")
        result = runner("_Raises").run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(
            [(candidate.plugin_id, candidate.reason) for candidate in result.candidates],
            [("p0", "plugin_exception")],
        )

        # missing_input: invoke. Without an observation the plugin is still called.
        self.assertEqual(FAILURE_POLICY.missing_input, "invoke")
        _RecordsObservation.seen = []
        runner("_RecordsObservation").run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        self.assertEqual(_RecordsObservation.seen, ["unavailable"])

        # reset: propagate.
        self.assertEqual(FAILURE_POLICY.reset, "propagate")
        with self.assertRaisesRegex(RuntimeError, "reset failed"):
            runner("_ResetRaises").reset({})


if __name__ == "__main__":
    unittest.main()
