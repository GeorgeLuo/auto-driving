"""The memory report and ledger summary names live in interface.py."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.memory.interface import (
    BOUNDS,
    EPOCH_ID,
    HEALTH,
    LEDGER_HEALTH_EMPTY,
    LEDGER_HEALTH_HEALTHY,
    LEDGER_HEALTH_VALUES,
    LEDGER_SUMMARY_KEYS,
    MEMORY_REPORT_SCHEMA,
    RECORD_COUNT,
    MemoryPluginReport,
    MemoryReport,
)
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.shared_memory import SharedMemory


class _SummaryMemory:
    plugin_id = "summary_memory"

    def update(self, context, observation) -> None:
        return None

    def reset(self, shared_memory: SharedMemory) -> None:
        return None

    def status(self, shared_memory: SharedMemory | None) -> dict:
        return {
            EPOCH_ID: "epoch-1",
            HEALTH: LEDGER_HEALTH_EMPTY,
            BOUNDS: {"max_records": 4},
            RECORD_COUNT: 0,
            "records": [],
        }


class MemoryInterfaceTests(unittest.TestCase):
    def test_ledger_summary_names_are_the_four_tooling_reads(self) -> None:
        self.assertEqual(
            LEDGER_SUMMARY_KEYS,
            ("epoch_id", "health", "bounds", "record_count"),
        )
        self.assertEqual(
            LEDGER_HEALTH_VALUES,
            frozenset({LEDGER_HEALTH_EMPTY, LEDGER_HEALTH_HEALTHY}),
        )
        self.assertEqual(MEMORY_REPORT_SCHEMA, "memory_report_v0")

    def test_report_round_trips_and_drops_a_non_dict_state(self) -> None:
        report = MemoryReport(
            schema=MEMORY_REPORT_SCHEMA,
            plugins=(
                MemoryPluginReport(
                    plugin_id="bounded_evidence",
                    state={EPOCH_ID: "epoch-2", RECORD_COUNT: 1},
                ),
                MemoryPluginReport(plugin_id="other", state=None),
            ),
        )

        self.assertEqual(MemoryReport.from_dict(report.to_dict()), report)
        parsed = MemoryPluginReport.from_dict({"plugin_id": "other", "state": ["nope"]})
        self.assertIsNone(parsed.state)
        self.assertEqual(
            MemoryReport.from_dict({}).to_dict(),
            {"schema": MEMORY_REPORT_SCHEMA, "plugins": []},
        )

    def test_runner_report_is_the_interface_dict(self) -> None:
        runner = MemoryRunner.from_plugins({"summary_memory": _SummaryMemory()})
        state = _SummaryMemory().status(None)

        self.assertEqual(
            runner.report(),
            MemoryReport(
                schema=MEMORY_REPORT_SCHEMA,
                plugins=(MemoryPluginReport(plugin_id="summary_memory", state=state),),
            ).to_dict(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
