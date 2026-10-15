"""highest_confidence plan plugin tests."""

from __future__ import annotations

import unittest

from implementations.decision_cycle.plan.plugins.highest_confidence.plugin import select_highest_confidence_plan
from tests.deterministic.autonomy.decision_cycle.action_proposal_plan_fixtures import (
    _active_proposal,
)


class SelectorTests(unittest.TestCase):
    def test_higher_confidence_wins(self) -> None:
        a = _active_proposal(plugin_id="a_plugin", confidence=0.4, steering=0.1)
        b = _active_proposal(plugin_id="b_plugin", confidence=0.9, steering=-0.1)
        plan = select_highest_confidence_plan(
            frame_id="frame_001",
            timestamp_ms=1,
            candidates=[a, b],
            selector_id="highest_confidence",
        )
        self.assertEqual(plan.status, "selected")
        self.assertEqual(plan.selected_proposal_id, b.proposal_id)

    def test_tie_break_plugin_id(self) -> None:
        a = _active_proposal(plugin_id="aaa", confidence=0.5)
        b = _active_proposal(plugin_id="bbb", confidence=0.5)
        plan = select_highest_confidence_plan(
            frame_id="frame_001",
            timestamp_ms=1,
            candidates=[b, a],
            selector_id="highest_confidence",
        )
        self.assertEqual(plan.selected_proposal_id, a.proposal_id)


if __name__ == "__main__":
    unittest.main()
