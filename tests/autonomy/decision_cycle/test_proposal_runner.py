from __future__ import annotations

import unittest

from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.proposal.interface import FAILURE_POLICY, PROPOSAL_SCHEMA
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.plugins import PluginDefinition
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


class _InvalidReturn(_ConfiguredProposal):
    def propose(self, source, shared_memory):
        return None


class _DeclaredError(_ConfiguredProposal):
    def propose(self, source, shared_memory) -> ActionProposal:
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=source.frame_id,
            lifecycle="error",
            freshness="none",
            confidence=0.0,
            reason="invalid_evidence",
            command=None,
            available=False,
        )


class _CorruptsCandidate(_ConfiguredProposal):
    def propose(self, source, shared_memory) -> ActionProposal:
        candidate = super().propose(source, shared_memory)
        object.__setattr__(candidate, "available", True)
        return candidate


class _RecordsSource(_ConfiguredProposal):
    def propose(self, source, shared_memory) -> ActionProposal:
        self.source = source
        self.shared_memory = shared_memory
        return super().propose(source, shared_memory)


class _RecordsReset(_ConfiguredProposal):
    def reset(self, shared_memory) -> None:
        self.reset_map = shared_memory


class _ResetsWithoutMap(_ConfiguredProposal):
    reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1


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

    def test_plugin_report_keeps_isolated_errors_separate_from_step_failures(self) -> None:
        runner = ProposalRunner.from_plugins({
            "raises": _Raises(plugin_id="raises"),
            "invalid": _InvalidReturn(plugin_id="invalid"),
            "declared": _DeclaredError(plugin_id="declared"),
            "healthy": _ConfiguredProposal(plugin_id="healthy"),
        })
        self.assertTrue(all(
            record["duration_ms"] is None and record["error"] is None
            for record in runner.plugin_report()["plugins"]
        ))

        result = runner.run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(
            [candidate.plugin_id for candidate in result.candidates],
            ["raises", "invalid", "declared", "healthy"],
        )
        records = runner.status()["plugin_report"]["plugins"]
        self.assertEqual([record["error"] for record in records], [
            "RuntimeError: propose failed", "plugin_invalid_return", "invalid_evidence", None,
        ])
        self.assertTrue(all(record["duration_ms"] >= 0 for record in records))
        self.assertEqual(runner.status()["failure_count"], 0)
        self.assertIsNone(runner.status()["last_error"])

        runner.reset({})
        self.assertTrue(all(
            record["duration_ms"] is None and record["error"] is None
            for record in runner.plugin_report()["plugins"]
        ))

    def test_admission_failure_reports_only_the_plugins_that_ran(self) -> None:
        runner = ProposalRunner.from_plugins({
            "healthy": _ConfiguredProposal(plugin_id="healthy"),
            "corrupt": _CorruptsCandidate(plugin_id="corrupt"),
            "later": _ConfiguredProposal(plugin_id="later"),
        })
        result = runner.run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "action_proposal_matrix_violated")
        self.assertEqual(result.candidates, ())
        records = runner.plugin_report()["plugins"]
        self.assertIsNotNone(records[0]["duration_ms"])
        self.assertIsNone(records[0]["error"])
        self.assertIsNotNone(records[1]["duration_ms"])
        self.assertIn("ActionProposalMatrixError", records[1]["error"])
        self.assertEqual(records[2], {"plugin_id": "later", "duration_ms": None, "error": None})
        self.assertEqual(runner.status()["failure_count"], 1)

    def test_recovery_and_source_failure_do_not_reuse_old_plugin_records(self) -> None:
        plugin = _ConfiguredProposal(plugin_id="p0")
        runner = ProposalRunner.from_plugins({"p0": plugin})
        original = plugin.propose
        plugin.propose = _Raises(plugin_id="p0").propose
        runner.run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        old_report = runner.plugin_report()
        plugin.propose = original
        runner.run(frame_id="frame_002", frame_index=1, timestamp_ms=2)
        self.assertIsNone(runner.plugin_report()["plugins"][0]["error"])
        self.assertEqual(old_report["plugins"][0]["error"], "RuntimeError: propose failed")

        result = runner.run(
            frame_id="frame_003", frame_index=2, timestamp_ms=3, observation=object(),
        )
        self.assertEqual(result.reason, "decision_data_source_invalid")
        self.assertEqual(runner.plugin_report()["plugins"], [
            {"plugin_id": "p0", "duration_ms": None, "error": None},
        ])

    def test_changed_selection_keeps_records_only_for_retained_instances(self) -> None:
        runner = ProposalRunner.from_activation(_activation(["p0", "p1"], count=2))
        runner.run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        original = runner.plugins["p1"]
        record = runner.plugin_report()["plugins"][1]
        runner.plugin_manager.select([
            runner.plugin_manager.selected[1],
            PluginDefinition(
                "proposal", "p0", _spec("_ConfiguredProposal"),
                config={"plugin_id": "p0", "reason": "new_config"},
            ),
        ])
        runner.apply_selection({})
        self.assertIs(runner.plugins["p1"], original)
        self.assertEqual(runner.plugin_report()["plugins"], [
            record, {"plugin_id": "p0", "duration_ms": None, "error": None},
        ])

        runner.plugin_manager.remove("p1")
        runner.apply_selection({})
        runner.plugin_manager.add("p1")
        runner.apply_selection({})
        self.assertIsNot(runner.plugins["p1"], original)
        self.assertIsNone(runner.plugin_report()["plugins"][1]["duration_ms"])

    def test_evidence_audit_uses_first_declared_key_and_plugins_share_the_host_map(self) -> None:
        first = _RecordsSource(plugin_id="first")
        second = _RecordsSource(plugin_id="second")
        first.evidence_key = "first.evidence"
        second.evidence_key = "second.evidence"
        shared_memory = {first.evidence_key: (), second.evidence_key: None}
        runner = ProposalRunner.from_plugins({"first": first, "second": second})
        runner.run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1, shared_memory=shared_memory,
        )
        self.assertEqual(first.source.evidence.status, "ready")
        self.assertEqual(second.source.evidence.status, "ready")
        self.assertIsNot(first.source, second.source)
        self.assertIs(first.shared_memory, shared_memory)
        self.assertIs(second.shared_memory, shared_memory)

        runner.plugin_manager.select(["second", "first"])
        runner.run(
            frame_id="frame_002", frame_index=1, timestamp_ms=2, shared_memory=shared_memory,
        )
        self.assertEqual(first.source.evidence.status, "unavailable")
        self.assertEqual(second.source.evidence.status, "unavailable")

    def test_optional_reset_receives_the_host_map_on_reset_and_selection_removal(self) -> None:
        mapped = _RecordsReset(plugin_id="mapped")
        unmapped = _ResetsWithoutMap(plugin_id="unmapped")
        runner = ProposalRunner.from_plugins({"mapped": mapped, "unmapped": unmapped})
        first_map = {"epoch": 1}
        runner.reset(first_map)
        self.assertIs(mapped.reset_map, first_map)
        self.assertEqual(unmapped.reset_count, 1)

        second_map = {"epoch": 2}
        runner.plugin_manager.select([])
        runner.apply_selection(second_map)
        self.assertIs(mapped.reset_map, second_map)
        self.assertEqual(unmapped.reset_count, 2)
        self.assertEqual(runner.plugin_report()["plugins"], [])


if __name__ == "__main__":
    unittest.main()
