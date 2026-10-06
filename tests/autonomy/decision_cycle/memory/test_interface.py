"""The memory report and ledger summary names live in interface.py."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
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


class _OwnedSummaryMemory:
    plugin_id = "owned_summary"

    def __init__(self) -> None:
        self.state = {
            EPOCH_ID: "epoch-1",
            HEALTH: LEDGER_HEALTH_EMPTY,
            BOUNDS: {"max_records": 4},
            RECORD_COUNT: 0,
            "records": [],
        }

    def update(self, context, observation) -> None:
        self.state[RECORD_COUNT] += 1
        self.state[HEALTH] = LEDGER_HEALTH_HEALTHY
        self.state["records"].append({"properties": {"frame_id": context.frame_id}})
        context.shared_memory["owned_summary.state"] = self.state

    def reset(self, shared_memory: SharedMemory) -> None:
        return None

    def status(self, shared_memory: SharedMemory | None) -> dict:
        return self.state


class MemoryInterfaceTests(unittest.TestCase):
    def test_serializing_a_report_detaches_nested_plugin_state(self) -> None:
        plugin = _OwnedSummaryMemory()
        runner = MemoryRunner.from_plugins({plugin.plugin_id: plugin})
        report = runner.update(DecisionFrameContext("original", 1, 100, shared_memory={}), None)
        serialized = report["plugins"][0]["state"]
        serialized[BOUNDS]["max_records"] = 99
        serialized["records"][0]["properties"]["frame_id"] = "reader-change"

        self.assertEqual(plugin.state[BOUNDS]["max_records"], 4)
        self.assertEqual(plugin.state["records"][0]["properties"]["frame_id"], "original")
        self.assertEqual(runner.report()["plugins"][0]["state"], plugin.state)

    def test_later_updates_do_not_change_a_previous_memory_report(self) -> None:
        plugin = _OwnedSummaryMemory()
        runner = MemoryRunner.from_plugins({plugin.plugin_id: plugin})
        shared = {}
        first = runner.update(DecisionFrameContext("first", 1, 100, shared_memory=shared), None)
        second = runner.update(DecisionFrameContext("second", 2, 200, shared_memory=shared), None)

        self.assertEqual(first["plugins"][0]["state"][RECORD_COUNT], 1)
        self.assertEqual(len(first["plugins"][0]["state"]["records"]), 1)
        self.assertEqual(first["plugins"][0]["state"]["records"][0]["properties"]["frame_id"], "first")
        self.assertEqual(second["plugins"][0]["state"][RECORD_COUNT], 2)
        self.assertEqual(shared["owned_summary.state"], plugin.state)

    def test_ledger_summary_names_are_the_four_tooling_reads(self) -> None:
        self.assertEqual(
            LEDGER_SUMMARY_KEYS,
            ("epoch_id", "health", "bounds", "record_count"),
        )
        self.assertEqual(
            LEDGER_HEALTH_VALUES,
            frozenset({LEDGER_HEALTH_EMPTY, LEDGER_HEALTH_HEALTHY}),
        )
        self.assertEqual(MEMORY_REPORT_SCHEMA, "memory_report_v1")

    def test_schema_reads_the_declared_policy_and_a_missing_ledger_key_is_null(self) -> None:
        from autonomy.decision_cycle.memory.interface import (
            FAILURE_POLICY_FIELDS,
            MEMORY_SCHEMA,
            failure_policy,
        )
        from autonomy.decision_cycle.perception.interface import (
            FAILURE_POLICY_FIELDS as PERCEPTION_FAILURE_FIELDS,
        )
        from cli.automa_cli.memory_report import ledger_summary
        from implementations.decision_cycle.catalog import selection_activation

        schema = MemoryRunner.from_activation(selection_activation("memory")).describe_schema()
        self.assertEqual(schema["schema"], MEMORY_SCHEMA)
        self.assertEqual(tuple(schema["failure_policy"]), FAILURE_POLICY_FIELDS)
        self.assertEqual(FAILURE_POLICY_FIELDS, PERCEPTION_FAILURE_FIELDS)
        self.assertEqual(schema["failure_policy"], failure_policy())
        self.assertEqual(schema["failure_policy"]["update"], "stop_cycle")
        self.assertIsNone(ledger_summary({EPOCH_ID: "epoch-1"})[RECORD_COUNT])

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
            evidence_publisher="bounded_evidence",
        )

        self.assertEqual(MemoryReport.from_dict(report.to_dict()), report)
        self.assertEqual(report.to_dict()["evidence_publisher"], "bounded_evidence")
        parsed = MemoryPluginReport.from_dict({"plugin_id": "other", "state": ["nope"]})
        self.assertIsNone(parsed.state)
        self.assertEqual(
            MemoryReport.from_dict({}).to_dict(),
            {"schema": MEMORY_REPORT_SCHEMA, "plugins": [], "evidence_publisher": None},
        )
        self.assertIsNone(MemoryReport.from_dict({"evidence_publisher": 3}).evidence_publisher)

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
