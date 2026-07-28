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

if __name__ == "__main__":
    unittest.main()
