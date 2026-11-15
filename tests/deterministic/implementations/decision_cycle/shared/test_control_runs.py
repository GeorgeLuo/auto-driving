"""Applied-run retention must follow host writes, not plan selections."""

import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import CONTROL_APPLICATION_LIMIT, ControlApplication
from autonomy.shared_memory import read_shared_memory_file, shared_memory_document
from implementations.decision_cycle.shared.control_runs import (
    CONTROL_RUNS_KEY, STATE_KEY, control_runs_status, read_control_runs, record_control_runs,
)


def application(at, *, by="query", plan=None, command=(1, 0), mode="autonomy", applied=True):
    metadata = {} if by is None else {"plugin_id": by}
    if plan is not None:
        metadata["plan_metadata"] = plan
    return ControlApplication(
        f"f{at}", mode, applied, "drive" if by else "decision-expired",
        AutonomyControl(throttle=command[0], steering=command[1], metadata=metadata), at,
    )


class ControlRunsTests(unittest.TestCase):
    def setUp(self):
        self.memory = {CONTROL_APPLICATIONS_KEY: ()}
        record_control_runs(self.memory)

    def consume(self, *window):
        self.memory[CONTROL_APPLICATIONS_KEY] = window
        record_control_runs(self.memory)
        return read_control_runs(self.memory)

    def test_first_cycle_skips_writes_from_previous_pipeline(self):
        memory = {CONTROL_APPLICATIONS_KEY: (application(0), application(10))}
        self.assertIsNone(read_control_runs(memory))
        record_control_runs(memory)
        self.assertEqual(read_control_runs(memory), ())
        memory[CONTROL_APPLICATIONS_KEY] += (application(20),)
        record_control_runs(memory)
        self.assertEqual(read_control_runs(memory)[0]["start_ms"], 20)

    def test_content_cursor_survives_copies_and_uses_last_occurrence(self):
        first, second = application(10), application(20, command=(-1, 1))
        self.consume(first)
        self.consume(first, second)
        original = read_control_runs(self.memory)
        # Object identity and timestamps cannot be cursors: replay rebuilds
        # objects, and equal-content records may occur more than once.
        self.consume(deepcopy(second), application(30, by=None), deepcopy(second))
        self.assertEqual(read_control_runs(self.memory), original)
        self.assertEqual(control_runs_status(self.memory)["gaps"], 0)

    def test_rolled_window_counts_a_gap_and_consumes_entire_window(self):
        self.consume(application(0, command=(-1, 0)))
        window = tuple(application(at, command=(1, at % 2)) for at in range(2, CONTROL_APPLICATION_LIMIT + 2))
        runs = self.consume(*window)
        self.assertEqual(control_runs_status(self.memory)["gaps"], 1)
        self.assertEqual(len(runs), CONTROL_APPLICATION_LIMIT + 1)
        self.assertEqual(runs[-1]["start_ms"], CONTROL_APPLICATION_LIMIT + 1)
        self.consume(*window)
        self.assertEqual(control_runs_status(self.memory)["gaps"], 1)

    def test_runs_extend_and_split_on_proposer_plan_or_command(self):
        window = [application(0)]
        self.consume(*window)
        previous = read_control_runs(self.memory)
        for item in (application(10), application(20, by="sweep"),
                     application(30, by="sweep", plan={"policy": {"epoch": 1}}),
                     application(40, by="sweep", plan={"policy": {"epoch": 1}}, command=(-1, 1))):
            window.append(item)
            self.consume(*window)
        runs = read_control_runs(self.memory)
        self.assertEqual([(r["start_ms"], r["end_ms"]) for r in runs],
                         [(0, 20), (20, 30), (30, 40), (40, None)])
        self.assertEqual(previous[0]["end_ms"], None)
        runs[-1]["plan"]["policy"]["epoch"] = 99
        self.assertEqual(read_control_runs(self.memory)[-1]["plan"]["policy"]["epoch"], 1)

    def test_host_stops_and_other_modes_close_without_opening_runs(self):
        for stop in (application(20, by=None, command=(0, 0)), application(20, mode="manual"),
                     application(20, mode="observe_only")):
            with self.subTest(mode=stop.mode, by=stop.control.metadata):
                memory = {CONTROL_APPLICATIONS_KEY: ()}
                record_control_runs(memory)
                memory[CONTROL_APPLICATIONS_KEY] = (application(10), stop)
                record_control_runs(memory)
                self.assertEqual([(r["start_ms"], r["end_ms"]) for r in read_control_runs(memory)], [(10, 20)])
        self.consume(application(10, command=(0, 0)))
        self.assertEqual(read_control_runs(self.memory)[0]["command"], [0, 0])

    def test_unapplied_writes_neither_close_nor_open_a_run(self):
        first = application(10)
        self.consume(first, replace(application(20, by=None), applied=False), application(30, applied=False))
        self.assertEqual([(r["start_ms"], r["end_ms"]) for r in read_control_runs(self.memory)], [(10, None)])

    def test_backwards_clock_never_closes_before_start(self):
        runs = self.consume(application(100), application(50, command=(-1, 0)), application(25, by=None))
        self.assertEqual([(r["start_ms"], r["end_ms"]) for r in runs], [(100, 100), (50, 50)])

    def test_only_last_forty_runs_are_retained_in_write_order(self):
        for at in range(45):
            self.consume(*(application(i, command=(1, i % 2)) for i in range(max(0, at - 31), at + 1)))
        runs = read_control_runs(self.memory)
        self.assertEqual(len(runs), 40)
        self.assertEqual([r["start_ms"] for r in runs], list(range(5, 45)))
        self.assertIsNone(runs[-1]["end_ms"])

    def test_publication_is_rebuilt_from_state_after_memory_file_round_trip(self):
        first = application(0, plan={"policy": {"epoch": 0}, "options": [1, {}]})
        self.consume(first)
        self.memory[CONTROL_RUNS_KEY] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            path.write_text(json.dumps(shared_memory_document(self.memory)))
            self.memory, _ = read_shared_memory_file(path)
        runs = self.consume(ControlApplication.from_dict(first.to_dict()), application(20, by=None))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["end_ms"], 20)
        self.assertEqual(control_runs_status(self.memory)["gaps"], 0)
        self.assertEqual(self.memory[STATE_KEY]["runs"], list(runs))

    def test_unknown_window_preserves_history_and_cursor_and_reports_unknown(self):
        self.consume(application(10))
        before = control_runs_status(self.memory)
        self.memory.pop(CONTROL_APPLICATIONS_KEY)
        record_control_runs(self.memory)
        after = control_runs_status(self.memory)
        self.assertFalse(after.pop("window_known"))
        before.pop("window_known")
        self.assertEqual(after, before)
        self.assertEqual(list(read_control_runs(self.memory)), before["runs"])
        memory = {}
        record_control_runs(memory)
        self.assertFalse(control_runs_status(memory)["initialized"])
        memory[CONTROL_APPLICATIONS_KEY] = (application(20),)
        record_control_runs(memory)
        self.assertEqual(read_control_runs(memory), ())  # first known window is still pre-start
