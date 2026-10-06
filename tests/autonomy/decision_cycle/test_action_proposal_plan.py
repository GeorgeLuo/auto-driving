from __future__ import annotations
import unittest
from autonomy.decision_cycle.plan.highest_confidence import select_highest_confidence_plan
from autonomy.decision_cycle.proposal.values import (
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
    synthetic_error_proposal,
)
from autonomy.serialization import canonical_json_bytes
from autonomy.decision_cycle.action.hold import idle_output
from autonomy.decision_cycle.action.values import proposed_equals_authorized
from tests.autonomy.decision_cycle.action_proposal_plan_fixtures import (
    _active_proposal,
)
from tests.support.action_fixtures import decision_chain, packaged_decision_chain


class ActionProposalMatrixTests(unittest.TestCase):

    def test_runner_accepts_unrelated_proposal_without_avoidance_config(self) -> None:
        engine = decision_chain(
            plugins={
                "cruise": lambda source, shared_memory: _active_proposal(
                    plugin_id="cruise", frame_id=source.frame_id
                )
            },
        )
        result = engine.run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1000
        )
        control = result.control
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.plan.selected_candidate().plugin_id, "cruise")
        self.assertEqual(control.throttle, 0.0)


    def test_proposed_equals_authorized_table(self) -> None:
        idle = idle_output()
        self.assertTrue(proposed_equals_authorized(None, idle))
        zero = ProposedVehicleCommand(steering=0.0, throttle=0.0, gear="hold")
        self.assertTrue(proposed_equals_authorized(zero, idle))
        nonzero = ProposedVehicleCommand(steering=0.35, throttle=0.0, gear="hold")
        self.assertFalse(proposed_equals_authorized(nonzero, idle))
        applied = {"steering": 0.35, "throttle": 0.0, "confidence": 0.9, "reason": "x"}
        self.assertTrue(proposed_equals_authorized(nonzero, applied))
        self.assertFalse(proposed_equals_authorized(None, applied))


class SelectorTests(unittest.TestCase):
    def test_higher_confidence_wins(self) -> None:
        a = _active_proposal(plugin_id="a_plugin", confidence=0.4, steering=0.1)
        # need valid plugin ids in grammar - a_plugin ok
        b = _active_proposal(plugin_id="b_plugin", confidence=0.9, steering=-0.1)
        # fix proposal construction - plugin ids must match pattern
        plan = select_highest_confidence_plan(
            frame_id="frame_001",
            timestamp_ms=1,
            candidates=[a, b],
        )
        self.assertEqual(plan.status, "selected")
        self.assertEqual(plan.selected_proposal_id, b.proposal_id)

    def test_tie_break_plugin_id(self) -> None:
        a = _active_proposal(plugin_id="aaa", confidence=0.5)
        b = _active_proposal(plugin_id="bbb", confidence=0.5)
        plan = select_highest_confidence_plan(
            frame_id="frame_001", timestamp_ms=1, candidates=[b, a]
        )
        self.assertEqual(plan.selected_proposal_id, a.proposal_id)


class RunnerBoundaryTests(unittest.TestCase):

    def test_prior_frame_proposal_not_selected(self) -> None:
        from autonomy.decision_cycle.proposal.inputs import DecisionDataSource

        stale = _active_proposal(frame_id="frame_001", confidence=0.99)

        def bad_plugin(source: DecisionDataSource, shared_memory) -> ActionProposal:
            return stale  # wrong frame

        engine = decision_chain(
            plugins={"avoid_recent_obstruction": bad_plugin},
        )
        result = engine.run(
            frame_id="frame_002", frame_index=2, timestamp_ms=2000
        )
        control = result.control
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        candidate = result.plan.candidates[0]
        self.assertEqual(candidate.lifecycle, "error")
        self.assertEqual(candidate.reason, "plugin_invalid_return")
        self.assertEqual(candidate.proposal_id, "avoid_recent_obstruction:frame_002")
        self.assertEqual(result.plan.status, "idle")
        self.assertFalse(result.authority.proposed_applied)
        self.assertEqual(control.steering, 0.0)

    def test_empty_selection_plans_idle_and_holds(self) -> None:
        result = decision_chain(plugins={}).run(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(result.status, "ok")
        assert result.plan is not None
        self.assertEqual(result.plan.status, "idle")
        self.assertEqual(result.plan.candidates, ())
        self.assertFalse(result.authority.proposed_applied)
        self.assertEqual(result.control.steering, 0.0)
        self.assertEqual(result.control.throttle, 0.0)

    def test_plan_takes_more_than_four_max_size_candidates(self) -> None:
        # Six *exact* 4096-byte proposals with max 64-char plugin ids, max
        # frame_id, max timestamp, and exact 1024-byte plan metadata. Each
        # candidate is bounded on its own; the plan has no count limit.
        plugins = [f"p{i}" + ("x" * 62) for i in range(6)]
        self.assertTrue(all(len(plugin_id) == 64 for plugin_id in plugins))
        frame = "f" * 64
        candidates = []
        reason = "r" * 240
        assumptions = tuple(("a" + ("b" * 62)) for _ in range(5))
        refs = tuple(
            SourceRef(
                kind="memory_record",
                id=("i" * 120),
                frame_id=frame,
                note=("n" * 64),
            )
            for _ in range(8)
        )

        def build(plugin_id: str, pad: int) -> ActionProposal:
            return ActionProposal(
                plugin_id=plugin_id,
                frame_id=frame,
                lifecycle="fresh",
                freshness="fresh",
                confidence=0.5,
                reason=reason,
                command=ProposedVehicleCommand(steering=0.1, throttle=0.0, gear="hold"),
                assumptions=assumptions,
                source_refs=refs,
                available=True,
                metadata={"pad": "x" * pad},
            )

        for plugin_id in plugins:
            exact = None
            for pad in range(0, 300):
                try:
                    prop = build(plugin_id, pad)
                except ValueError:
                    break
                size = canonical_json_bytes(prop.to_dict())
                if size == 4096:
                    exact = prop
                    break
            self.assertIsNotNone(
                exact, f"could not craft 4096-byte proposal for {plugin_id}"
            )
            assert exact is not None
            self.assertEqual(canonical_json_bytes(exact.to_dict()), 4096)
            self.assertEqual(len(exact.plugin_id), 64)
            candidates.append(exact)

        plan_metadata = None
        for pad in range(0, 1200):
            meta = {"pad": "m" * pad}
            if canonical_json_bytes(meta) == 1024:
                plan_metadata = meta
                break
        self.assertIsNotNone(plan_metadata)
        assert plan_metadata is not None
        self.assertEqual(canonical_json_bytes(plan_metadata), 1024)

        plan = select_highest_confidence_plan(
            frame_id=frame,
            timestamp_ms=9_007_199_254_740_991,
            candidates=candidates,
            metadata=plan_metadata,
        )
        self.assertEqual(len(plan.candidates), 6)
        self.assertEqual(plan.status, "selected")
        self.assertTrue(plan.selected_proposal_id.endswith(":" + frame))
        self.assertEqual(canonical_json_bytes(plan.to_dict()["metadata"]), 1024)


    def test_metadata_round_trip_preserves_object_array_identity(self) -> None:
        cases = (
            {},
            {"empty": {}},
            {"arr": []},
            {"nested": {"a": [], "b": {}}},
            {"pairs": [["x", 1], ["y", 2]]},
        )
        for meta in cases:
            with self.subTest(meta=meta):
                prop = ActionProposal(
                    plugin_id="avoid_recent_obstruction",
                    frame_id="frame_001",
                    lifecycle="inactive",
                    freshness="none",
                    confidence=0.0,
                    reason="noop",
                    command=None,
                    available=False,
                    metadata=meta,
                )
                self.assertEqual(prop.to_dict()["metadata"], meta)
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
                metadata={"set": {1, 2}},
            )
        # Empty default metadata serializes as object, not array.
        err = synthetic_error_proposal(
            plugin_id="p", frame_id="f", reason="plugin_exception"
        )
        self.assertEqual(err.to_dict()["metadata"], {})


    def test_host_application_bad_type_is_not_recorded(self) -> None:
        engine = packaged_decision_chain()
        result = engine.run(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=1,
            host_application="bad",  # type: ignore[arg-type]
        )
        control = result.control
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.authority.host_application.status, "unavailable")
        self.assertEqual(
            result.authority.authorized_output["reason"], "hold-idle"
        )
        self.assertFalse(result.authority.proposed_applied)
        self.assertEqual(control.steering, 0.0)


    def test_more_than_four_plugins_each_propose(self) -> None:
        ids = [f"p{index}" for index in range(6)]
        engine = decision_chain(
            plugins={
                plugin_id: (
                    lambda source, shared_memory, plugin_id=plugin_id: _active_proposal(
                        plugin_id=plugin_id, frame_id=source.frame_id
                    )
                )
                for plugin_id in ids
            },
        )
        result = engine.run(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        self.assertEqual(result.status, "ok")
        assert result.plan is not None
        self.assertEqual([c.plugin_id for c in result.plan.candidates], ids)
        self.assertEqual(result.plan.status, "selected")

    def test_plugin_none_wrong_id_and_exception(self) -> None:
        from autonomy.decision_cycle.proposal.inputs import DecisionDataSource

        def return_none(source: DecisionDataSource, shared_memory) -> ActionProposal:
            return None  # type: ignore[return-value]

        def wrong_id(source: DecisionDataSource, shared_memory) -> ActionProposal:
            return _active_proposal(plugin_id="other", frame_id=source.frame_id)

        def boom(source: DecisionDataSource, shared_memory) -> ActionProposal:
            raise RuntimeError("plugin crashed")

        for plugin_fn, reason in (
            (return_none, "plugin_invalid_return"),
            (wrong_id, "plugin_invalid_return"),
            (boom, "plugin_exception"),
        ):
            with self.subTest(reason=reason):
                engine = decision_chain(
                    plugins={"avoid_recent_obstruction": plugin_fn},
                )
                result = engine.run(
                    frame_id="frame_001", frame_index=0, timestamp_ms=1
                )
                control = result.control
                self.assertEqual(result.status, "ok")
                assert result.plan is not None
                self.assertEqual(len(result.plan.candidates), 1)
                self.assertEqual(result.plan.candidates[0].reason, reason)
                self.assertEqual(result.plan.candidates[0].lifecycle, "error")
                self.assertEqual(control.steering, 0.0)

