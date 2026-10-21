from __future__ import annotations

import unittest

from autonomy.decision_cycle.memory.beliefs import Belief, Check, Trial
from cli.automa_cli.learning_history import LEARNING_HISTORY_SCHEMA, LearningHistory, _leaves


def _status(t, beliefs=(), plugins=(), *, reset_count=0) -> dict:
    """A ``host_status()["autonomy"]`` payload with a memory step at frame time ``t``."""

    return {
        "last_cycle": {"context": {"timestamp_ms": t}},
        "steps": {"memory": {
            "reset_count": reset_count,
            "beliefs": {belief.belief_id: belief.to_dict() for belief in beliefs},
            "plugins": list(plugins),
        }},
    }


def _points(payload: dict, belief_id: str, path: str) -> list:
    return [[t, value] for _, t, value in payload["beliefs"][belief_id]["series"].get(path, [])]


def _trials(payload: dict, belief_id: str) -> dict:
    return {row["trial_id"]: (row["end"], row["reason"], row["outcome"], row["ended_t"])
            for row in payload["beliefs"][belief_id]["trials"]}


def _row(value=3.0, at_ms=100) -> Belief:
    return Belief("stop_row.row", value, "stop_row", at_ms)


class LeavesTests(unittest.TestCase):
    def test_numbers_strings_and_list_lengths_are_leaves_by_json_pointer(self) -> None:
        self.assertEqual(_leaves({
            "count": 2, "ready": True, "phase": "idle", "trials": [1, 2, 3],
            "missing": None, "at_ms": 5, "seen_at_ms": 6, "nan": float("nan"),
            "rows": {"a/b~c": 1.5, "nested": {"deep": False}},
        }), {
            "/count": 2, "/ready": 1, "/phase": "idle", "/trials#len": 3,
            "/rows/a~1b~0c": 1.5, "/rows/nested/deep": 0,
        })
        self.assertEqual(_leaves(4.5), {"": 4.5})


class LearningHistoryTests(unittest.TestCase):
    def test_a_status_without_memory_or_a_frame_time_adds_nothing(self) -> None:
        history = LearningHistory()
        self.assertFalse(history.ingest({"steps": {"memory": None}}))
        self.assertFalse(history.payload()["available"])
        self.assertFalse(history.ingest({"steps": {"memory": {"beliefs": {}}}}))
        payload = history.payload()
        self.assertEqual(payload["schema"], LEARNING_HISTORY_SCHEMA)
        self.assertTrue(payload["available"])
        self.assertEqual((payload["seq"], payload["now_ms"], payload["beliefs"]), (0, None, {}))

    def test_points_are_added_on_change_and_a_repeated_frame_is_skipped(self) -> None:
        history = LearningHistory()
        self.assertTrue(history.ingest(_status(100, [_row(3.0)])))
        self.assertFalse(history.ingest(_status(100, [_row(9.0)])))
        self.assertTrue(history.ingest(_status(200, [_row(3.0)])))
        self.assertTrue(history.ingest(_status(300, [_row(4.0, 300)])))

        payload = history.payload()
        self.assertEqual(payload["now_ms"], 300)
        self.assertEqual(_points(payload, "stop_row.row", "/value"), [[100, 3.0], [300, 4.0]])
        self.assertEqual(_points(payload, "stop_row.row", "/checks#len"), [[100, 0]])
        self.assertEqual(payload["beliefs"]["stop_row.row"]["current"]["value"], 4.0)
        self.assertNotIn("/updated_at_ms", payload["beliefs"]["stop_row.row"]["series"])
        self.assertEqual([event["kind"] for event in payload["events"]], ["belief_added"])

    def test_checks_arrive_once_and_trials_end_withdrawn_or_resolved(self) -> None:
        history = LearningHistory()
        belief = (Belief("approach.run_ms", 1200.0, "approach", 100)
                  .opened(Trial("run_1", "run_duration", 100, [1000.0, 1400.0]), 4)
                  .opened(Trial("run_2", "run_duration", 100, [1100.0, 1500.0]), 4)
                  .opened(Trial("look_1", "look", 100, 2.0), 4))
        history.ingest(_status(100, [belief]))
        self.assertEqual(_trials(history.payload(), "approach.run_ms"), {
            "run_1": ("open", None, None, None),
            "run_2": ("open", None, None, None),
            "look_1": ("open", None, None, None),
        })

        belief = (belief.resolved("run_1", "support", 1300.0, 200, 64)
                  .withdrawn(("run_2",), reason="edge_lost")
                  .resolved("look_1", "contradict", 3.0, 200, 64))
        history.ingest(_status(200, [belief]))
        payload = history.payload()
        self.assertEqual(_trials(payload, "approach.run_ms"), {
            "run_1": ("resolved", None, "support", 200),
            "run_2": ("withdrawn", "edge_lost", None, 200),
            "look_1": ("resolved", None, "contradict", 200),
        })
        checks = payload["beliefs"]["approach.run_ms"]["checks"]
        self.assertEqual([(check["t"], check["method"], check["outcome"]) for check in checks],
                         [(200, "run_duration", "support"), (200, "look", "contradict")])
        self.assertEqual(_points(payload, "approach.run_ms", "/withdrawals/run_duration/edge_lost"), [[200, 1]])
        self.assertEqual(_points(payload, "approach.run_ms", "/by_method/look/contradict"), [[200, 1]])

        # Identical checks are told apart by count, not dropped as repeats.
        repeat = Check(300, "look", "support", 2.0, 2.0)
        belief = belief.checked(repeat, 64).checked(repeat, 64)
        history.ingest(_status(300, [belief]))
        history.ingest(_status(400, [belief.checked(repeat, 64)]))
        history.ingest(_status(500, [belief.checked(repeat, 64)]))
        self.assertEqual([check["t"] for check in history.payload()["beliefs"]["approach.run_ms"]["checks"]],
                         [200, 200, 300, 300, 400])

    def test_resets_and_beliefs_that_leave_and_return_are_events(self) -> None:
        history = LearningHistory()
        belief = _row().opened(Trial("stop_1", "stop", 100, 3.0), 4)
        history.ingest(_status(100, [belief]))
        history.ingest(_status(200, []))
        payload = history.payload()
        self.assertIsNone(payload["beliefs"]["stop_row.row"]["current"])
        self.assertEqual(_trials(payload, "stop_row.row"), {"stop_1": ("removed", None, None, 200)})
        self.assertEqual(_points(payload, "stop_row.row", "/value"), [[100, 3.0], [200, None]])

        history.ingest(_status(50, [belief]))
        history.ingest(_status(60, [belief], reset_count=1))
        history.ingest(_status(70, [belief], reset_count=1))
        events = [(event["kind"], event["t"], event.get("belief_id"), event.get("previous_t"))
                  for event in history.payload()["events"]]
        self.assertEqual(events, [
            ("belief_added", 100, "stop_row.row", None),
            ("belief_removed", 200, "stop_row.row", None),
            ("reset", 50, None, 200),
            ("belief_added", 50, "stop_row.row", None),
            ("reset", 60, None, 50),
        ])
        # The returning belief's trial is open again, under a later row.
        self.assertEqual(_trials(history.payload(), "stop_row.row")["stop_1"], ("open", None, None, None))

    def test_since_returns_only_later_rows_and_every_current_record(self) -> None:
        history = LearningHistory()
        plugin = {"plugin_id": "stop_row", "update_count": 1, "failure_count": 0, "last_duration_ms": 0.5,
                  "last_error": None, "state": {"phase": "idle", "bracket_width": 4.0}}
        history.ingest(_status(100, [_row()], [plugin]))
        first = history.payload()
        history.ingest(_status(200, [_row(5.0, 200)], [{**plugin, "update_count": 2,
                                                        "state": {"phase": "testing", "bracket_width": 4.0}}]))
        later = history.payload(first["seq"])

        self.assertEqual(later["history_id"], first["history_id"])
        self.assertGreater(later["seq"], first["seq"])
        self.assertEqual(later["beliefs"]["stop_row.row"]["series"], {"/value": [[first["seq"] + 1, 200, 5.0]]})
        self.assertEqual(later["beliefs"]["stop_row.row"]["current"]["value"], 5.0)
        self.assertEqual(later["events"], [])
        plugins = later["plugins"]["stop_row"]
        self.assertEqual(plugins["series"], {"/state/phase": [[first["seq"] + 2, 200, "testing"]]})
        self.assertEqual(plugins["current"]["update_count"], 2)
        self.assertEqual(sorted(first["plugins"]["stop_row"]["series"]),
                         ["/failure_count", "/last_duration_ms", "/state/bracket_width", "/state/phase"])
        self.assertEqual(history.payload(later["seq"])["beliefs"]["stop_row.row"]["series"], {})

    def test_points_and_paths_are_bounded(self) -> None:
        history = LearningHistory(max_points=3, max_paths=4)
        for index in range(6):
            history.ingest(_status(100 * (index + 1), [Belief(
                "approach.ms_per_row", {str(row): 10.0 * row + index for row in range(5)}, "approach", 100,
            )]))
        track = history.payload()["beliefs"]["approach.ms_per_row"]
        self.assertEqual(len(track["series"]), 4)
        self.assertGreater(track["dropped_paths"], 0)
        for points in track["series"].values():
            self.assertLessEqual(len(points), 3)
        self.assertEqual(len(history.payload()["events"]), 1)


if __name__ == "__main__":
    unittest.main()
