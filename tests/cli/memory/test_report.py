from __future__ import annotations

import unittest

from cli.automa_cli.memory_report import memory_summary


class MemorySummaryTests(unittest.TestCase):
    def test_summary_carries_health_record_count_and_epoch(self) -> None:
        state = {"health": "healthy", "record_count": 3, "epoch_id": "epoch-2", "records": []}
        self.assertEqual(
            memory_summary(state),
            {"health": "healthy", "record_count": 3, "epoch_id": "epoch-2"},
        )

    def test_summary_without_a_state_is_empty(self) -> None:
        empty = {"health": None, "record_count": 0, "epoch_id": None}
        self.assertEqual(memory_summary(None), empty)
        self.assertEqual(memory_summary({}), empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
