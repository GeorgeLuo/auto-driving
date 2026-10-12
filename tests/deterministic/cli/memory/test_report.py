from __future__ import annotations

import unittest

from autonomy.decision_cycle.memory.interface import LEDGER_SUMMARY_KEYS, RECORD_COUNT
from cli.automa_cli.memory_report import ledger_summary, memory_summary


class MemorySummaryTests(unittest.TestCase):
    def test_summary_carries_health_record_count_and_epoch(self) -> None:
        state = {"health": "healthy", "record_count": 3, "epoch_id": "epoch-2", "records": []}
        self.assertEqual(
            memory_summary(state),
            {"health": "healthy", "record_count": 3, "epoch_id": "epoch-2"},
        )

    def test_ledger_summary_reads_the_interface_names(self) -> None:
        state = {
            "health": "healthy",
            "record_count": 3,
            "epoch_id": "epoch-2",
            "bounds": {"max_records": 8},
            "records": [],
        }
        self.assertEqual(set(ledger_summary(state)), set(LEDGER_SUMMARY_KEYS))
        self.assertEqual(
            ledger_summary(state),
            {
                "epoch_id": "epoch-2",
                "health": "healthy",
                "bounds": {"max_records": 8},
                "record_count": 3,
            },
        )
        self.assertEqual(
            ledger_summary(None),
            {"epoch_id": None, "health": None, "bounds": None, "record_count": None},
        )

    def test_summary_without_a_state_is_empty(self) -> None:
        empty = {"health": None, "record_count": 0, "epoch_id": None}
        self.assertEqual(memory_summary(None), empty)
        self.assertEqual(memory_summary({}), empty)
        self.assertIsNone(memory_summary({RECORD_COUNT: None})[RECORD_COUNT])


if __name__ == "__main__":
    unittest.main(verbosity=2)
