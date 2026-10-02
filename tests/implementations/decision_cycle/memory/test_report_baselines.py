from __future__ import annotations

import unittest

from tests.implementations.decision_cycle.memory.replay_fixtures import (
    bounded_evidence_replay,
    load_baseline,
    tracks_replay,
)


class MemoryReportBaselineTests(unittest.TestCase):
    """What the packaged memory plugins publish for a scripted input is pinned."""

    def test_bounded_evidence_report_matches_baseline(self) -> None:
        self.assertEqual(bounded_evidence_replay(), load_baseline("bounded_evidence_report"))

    def test_multi_obstruction_tracks_report_matches_baseline(self) -> None:
        self.assertEqual(tracks_replay(), load_baseline("multi_obstruction_tracks_report"))

    def test_replays_repeat_exactly(self) -> None:
        # A baseline only proves a change neutral if the replay itself is stable.
        self.assertEqual(bounded_evidence_replay(), bounded_evidence_replay())
        self.assertEqual(tracks_replay(), tracks_replay())


if __name__ == "__main__":
    unittest.main()
