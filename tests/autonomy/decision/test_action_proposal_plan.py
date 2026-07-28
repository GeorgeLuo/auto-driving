"""ActionProposal / ActionPlan / authority tests (M006-02, M006-03)."""

from __future__ import annotations

import unittest

from autonomy.decision.action_plan import select_action_plan
from autonomy.decision.action_proposal import (
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
    synthetic_error_proposal,
)
from autonomy.decision.memory import canonical_json_bytes
from autonomy.decision.shadow_authority import build_authority, proposed_equals_authorized
from autonomy.decision.shadow_ids import ShadowCycleInputError
from autonomy.decision.shadow_runner import ShadowProposalsConfig, ShadowProposalsEngine
from implementations.decision.catalog import create_shadow_proposals_engine


def _active_proposal(
    *,
    plugin_id: str = "avoid_recent_obstruction",
    frame_id: str = "frame_001",
    confidence: float = 0.5,
    steering: float = 0.35,
) -> ActionProposal:
    return ActionProposal(
        plugin_id=plugin_id,
        frame_id=frame_id,
        lifecycle="fresh",
        freshness="fresh",
        confidence=confidence,
        reason="test",
        command=ProposedVehicleCommand(steering=steering, throttle=0.0, gear="hold"),
        assumptions=("shadow_only",),
        source_refs=(
            SourceRef(kind="memory_record", id="r1", frame_id=frame_id, note="primary"),
        ),
        available=True,
    )


class ActionProposalMatrixTests(unittest.TestCase):
    def test_rejects_fresh_with_stale_freshness(self) -> None:
        with self.assertRaises(ValueError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="fresh",
                freshness="stale",
                confidence=0.5,
                reason="bad",
                command=ProposedVehicleCommand(steering=0.1, throttle=0.0, gear="hold"),
                source_refs=(SourceRef(kind="memory_record", id="r"),),
                available=True,
            )

    def test_rejects_nonfinite_confidence(self) -> None:
        with self.assertRaises(ValueError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="inactive",
                freshness="none",
                confidence=float("nan"),
                reason="x",
                command=None,
                available=False,
            )

    def test_rejects_confidence_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="inactive",
                freshness="none",
                confidence=1.5,
                reason="x",
                command=None,
                available=False,
            )

    def test_proposed_equals_authorized_table(self) -> None:
        self.assertTrue(proposed_equals_authorized(None))
        zero = ProposedVehicleCommand(steering=0.0, throttle=0.0, gear="hold")
        self.assertTrue(proposed_equals_authorized(zero))
        nonzero = ProposedVehicleCommand(steering=0.35, throttle=0.0, gear="hold")
        self.assertFalse(proposed_equals_authorized(nonzero))


class SelectorTests(unittest.TestCase):
    def test_higher_confidence_wins(self) -> None:
        a = _active_proposal(plugin_id="a_plugin", confidence=0.4, steering=0.1)
        # need valid plugin ids in grammar - a_plugin ok
        b = _active_proposal(plugin_id="b_plugin", confidence=0.9, steering=-0.1)
        # fix proposal construction - plugin ids must match pattern
        plan = select_action_plan(
            frame_id="frame_001",
            timestamp_ms=1,
            candidates=[a, b],
        )
        self.assertEqual(plan.status, "selected")
        self.assertEqual(plan.selected_proposal_id, b.proposal_id)

    def test_tie_break_plugin_id(self) -> None:
        a = _active_proposal(plugin_id="aaa", confidence=0.5)
        b = _active_proposal(plugin_id="bbb", confidence=0.5)
        plan = select_action_plan(
            frame_id="frame_001", timestamp_ms=1, candidates=[b, a]
        )
        self.assertEqual(plan.selected_proposal_id, a.proposal_id)


class RunnerBoundaryTests(unittest.TestCase):
    def test_invalid_frame_id_raises_before_cycle_result(self) -> None:
        engine = create_shadow_proposals_engine()
        with self.assertRaises(ShadowCycleInputError):
            engine.run_cycle(frame_id="😀", frame_index=0, timestamp_ms=1)

    def test_prior_frame_proposal_not_selected(self) -> None:
        from autonomy.decision.decision_data import DecisionDataSource

        stale = _active_proposal(frame_id="frame_001", confidence=0.99)

        def bad_plugin(source: DecisionDataSource) -> ActionProposal:
            return stale  # wrong frame

        engine = ShadowProposalsEngine(
            config=ShadowProposalsConfig(
                enabled_plugins=("avoid_recent_obstruction",),
                known_plugins=frozenset({"avoid_recent_obstruction"}),
            ),
            plugins={"avoid_recent_obstruction": bad_plugin},
        )
        result, control = engine.run_cycle(
            frame_id="frame_002", frame_index=2, timestamp_ms=2000
        )
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

    def test_empty_enabled_plugins_rejects_activation(self) -> None:
        with self.assertRaises(ValueError):
            ShadowProposalsConfig(enabled_plugins=())

    def test_max_legal_plan_builds(self) -> None:
        # Four *exact* 4096-byte proposals with max frame_id and max timestamp.
        plugins = ["p0", "p1", "p2", "p3"]
        frame = "f" * 64
        candidates = []
        reason = "r" * 240
        assumptions = tuple(("a" + ("b" * 62)) for _ in range(8))
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
                command=ProposedVehicleCommand(
                    steering=0.1, throttle=0.0, gear="hold"
                ),
                assumptions=assumptions,
                source_refs=refs,
                available=True,
                metadata={"pad": "x" * pad},
            )

        for plugin_id in plugins:
            # size0 is ~3975 with id_len=120 and 8 refs; pad metadata to 4096.
            exact = None
            for pad in range(0, 200):
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
            candidates.append(exact)

        plan = select_action_plan(
            frame_id=frame,
            timestamp_ms=9_007_199_254_740_991,
            candidates=candidates,
            metadata={"pad": "m" * 50},
        )
        size = canonical_json_bytes(plan.to_dict())
        self.assertLessEqual(size, 24_576)
        self.assertEqual(plan.status, "selected")
        self.assertTrue(plan.selected_proposal_id.endswith(":" + frame))

    def test_metadata_frozen_after_construction(self) -> None:
        prop = _active_proposal()
        # Frozen metadata must not grow after the size check.
        with self.assertRaises(Exception):
            prop.metadata["pad"] = "x" * 20_000  # type: ignore[index]
        # Backing store must also reject ordinary mutation (not only __setitem__).
        with self.assertRaises(Exception):
            prop.metadata._data["pad"] = "x" * 20_000  # type: ignore[index]
        with self.assertRaises(Exception):
            prop.metadata._data = {"pad": "x" * 20_000}  # type: ignore[misc]
        self.assertLessEqual(canonical_json_bytes(prop.to_dict()), 4096)

    def test_require_safe_int_rejects_coercion(self) -> None:
        from autonomy.decision.shadow_ids import require_safe_int

        for bad in (1.9, "12", True, False):
            with self.assertRaises(ValueError):
                require_safe_int(bad, field_name="timestamp_ms")

    def test_empty_reason_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="",
                command=None,
                available=False,
            )

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

    def test_contribution_plugin_id_must_match_selected(self) -> None:
        from autonomy.decision.action_plan import ActionPlan, PlanContribution

        a = _active_proposal(plugin_id="aaa")
        with self.assertRaises(ValueError):
            ActionPlan(
                frame_id="frame_001",
                timestamp_ms=1,
                status="selected",
                candidates=(a,),
                selected_proposal_id=a.proposal_id,
                contributions=(
                    PlanContribution(
                        proposal_id=a.proposal_id,
                        plugin_id="wrong",
                        weight=1.0,
                        role="selected",
                    ),
                ),
            )

    def test_host_application_bad_type_is_engine_error(self) -> None:
        engine = create_shadow_proposals_engine()
        result, control = engine.run_cycle(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=1,
            host_application="bad",  # type: ignore[arg-type]
        )
        self.assertEqual(result.status, "engine_error")
        self.assertEqual(result.reason, "engine_internal_error")
        self.assertIsNone(result.plan)
        self.assertEqual(
            result.authority.authorized_output["reason"], "shadow-only-idle"
        )
        self.assertFalse(result.authority.proposed_applied)
        self.assertEqual(control.steering, 0.0)

    def test_bound_rejections(self) -> None:
        with self.assertRaises(ValueError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="inactive",
                freshness="none",
                confidence=0.0,
                reason="r" * 241,
                command=None,
                available=False,
            )
        refs = tuple(
            SourceRef(kind="memory_record", id=f"r{i}") for i in range(17)
        )
        with self.assertRaises(ValueError):
            ActionProposal(
                plugin_id="avoid_recent_obstruction",
                frame_id="frame_001",
                lifecycle="stale",
                freshness="stale",
                confidence=0.0,
                reason="stale",
                command=None,
                source_refs=refs,
                available=False,
            )

    def test_invalid_activation_configs(self) -> None:
        with self.assertRaises(ValueError):
            ShadowProposalsConfig(steer_magnitude=-0.1)
        with self.assertRaises(ValueError):
            ShadowProposalsConfig(steer_magnitude=1.1)
        with self.assertRaises(ValueError):
            ShadowProposalsConfig(
                enabled_plugins=("a", "a"),
                known_plugins=frozenset({"a"}),
            )
        with self.assertRaises(ValueError):
            ShadowProposalsConfig(
                enabled_plugins=("missing",),
                known_plugins=frozenset({"a"}),
            )
        with self.assertRaises(ValueError):
            ShadowProposalsConfig(
                enabled_plugins=("a", "b", "c", "d", "e"),
                known_plugins=frozenset({"a", "b", "c", "d", "e"}),
            )

    def test_plugin_none_wrong_id_and_exception(self) -> None:
        from autonomy.decision.decision_data import DecisionDataSource

        def return_none(source: DecisionDataSource) -> ActionProposal:
            return None  # type: ignore[return-value]

        def wrong_id(source: DecisionDataSource) -> ActionProposal:
            return _active_proposal(plugin_id="other", frame_id=source.frame_id)

        def boom(source: DecisionDataSource) -> ActionProposal:
            raise RuntimeError("plugin crashed")

        for plugin_fn, reason in (
            (return_none, "plugin_invalid_return"),
            (wrong_id, "plugin_invalid_return"),
            (boom, "plugin_exception"),
        ):
            with self.subTest(reason=reason):
                engine = ShadowProposalsEngine(
                    config=ShadowProposalsConfig(
                        enabled_plugins=("avoid_recent_obstruction",),
                        known_plugins=frozenset({"avoid_recent_obstruction"}),
                    ),
                    plugins={"avoid_recent_obstruction": plugin_fn},
                )
                result, control = engine.run_cycle(
                    frame_id="frame_001", frame_index=0, timestamp_ms=1
                )
                self.assertEqual(result.status, "ok")
                assert result.plan is not None
                self.assertEqual(len(result.plan.candidates), 1)
                self.assertEqual(result.plan.candidates[0].reason, reason)
                self.assertEqual(result.plan.candidates[0].lifecycle, "error")
                self.assertEqual(control.steering, 0.0)

    def test_proposal_over_max_bytes_rejected(self) -> None:
        # Pad metadata until canonical size exceeds 4096.
        pad = "x" * 3800
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
                metadata={"pad": pad},
            )

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
                known_plugins=frozenset({"avoid_recent_obstruction"}),
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
                known_plugins=frozenset({"avoid_recent_obstruction"}),
            ),
            plugins={"avoid_recent_obstruction": corrupt_matrix},
        )
        result, _ = engine.run_cycle(
            frame_id="frame_001", frame_index=0, timestamp_ms=1
        )
        self.assertEqual(result.status, "engine_error")
        self.assertEqual(result.reason, "action_proposal_matrix_violated")
        self.assertIsNone(result.plan)

if __name__ == "__main__":
    unittest.main()
