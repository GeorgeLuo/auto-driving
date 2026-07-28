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
        # Four near-max proposals under field grammar must form a legal plan.
        plugins = ["p0", "p1", "p2", "p3"]
        frame = "f" * 64
        candidates = []
        reason = "r" * 240
        assumptions = ("shadow_only", "image_relative_only")
        refs = (
            SourceRef(
                kind="memory_record",
                id="primary",
                frame_id=frame,
                note="primary_obstruction",
            ),
        )
        for plugin_id in plugins:
            # Binary-search metadata padding to approach the 4096-byte ceiling.
            low, high = 1, 3500
            best = None
            while low <= high:
                mid = (low + high) // 2
                try:
                    prop = ActionProposal(
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
                        metadata={"pad": "x" * mid},
                    )
                except ValueError:
                    high = mid - 1
                    continue
                size = canonical_json_bytes(prop.to_dict())
                if size <= 4096:
                    best = prop
                    low = mid + 1
                else:
                    high = mid - 1
            assert best is not None
            candidates.append(best)
            size = canonical_json_bytes(best.to_dict())
            self.assertLessEqual(size, 4096)
            # Near ceiling: base fields + max metadata (1024) land well above 1.5 KiB.
            self.assertGreaterEqual(size, 1500)

        plan = select_action_plan(
            frame_id=frame,
            timestamp_ms=9_007_199_254_740_991,
            candidates=candidates,
            metadata={"pad": "m" * 200},
        )
        size = canonical_json_bytes(plan.to_dict())
        self.assertLessEqual(size, 24_576)
        self.assertEqual(plan.status, "selected")
        self.assertTrue(plan.selected_proposal_id.endswith(":" + frame))

if __name__ == "__main__":
    unittest.main()
