"""The validation script checks current workbench states for every frame."""

from contextlib import nullcontext
from copy import deepcopy
import unittest
from unittest.mock import patch

from scripts.validation import workbench_e2e as validation


class Workbench(validation.Workbench):
    def __init__(self, count, mutate=lambda state, position: None):
        self.frames = [{
            "frame_id": f"frame-{index}",
            "record": {"status": "ok", "plugin_runs": [{"plugin_id": "fixture", "signal_count": index}]},
        } for index in range(count)]
        self.latest = 0
        self.seeks = []
        self.mutate = mutate

    def state_at(self, position):
        state = {
            "run_id": "run", "position": position + 1,
            "latest_processed_position": self.latest,
            "progress": {"total": len(self.frames)},
            "current_frame": {"frame_id": self.frames[position]["frame_id"]},
            "active_perception_plugin_ids": ["fixture"],
            "machine_detail": {"pipeline": {"perception_preset": validation.PERCEPTION_PRESET}},
            "perception": deepcopy(self.frames[position]["record"]),
            "cycle": {
                "observation": None, "proposal": None, "plan": None,
                "action": {"status": "ok"},
                "memory": {"plugins": [{"plugin_id": "memory", "state": {"record_count": position}}]},
            },
        }
        self.mutate(state, position)
        return state

    def wait_for(self, condition):
        state = self.state_at(0)
        assert condition(state)
        return state

    def act(self, *, action, run_id, position=0, **fields):
        assert run_id == "run"
        if action == "seek":
            assert 0 <= position < len(self.frames)
            self.seeks.append(position)
            self.latest = max(self.latest, position)
        elif action == "select_plugins":
            assert fields == {"step": "perception", "active_plugin_ids": ["fixture"]}
        else:
            assert action == "pause"
        return self.state_at(position)


class WorkbenchValidationTests(unittest.TestCase):
    def check(self, workbench):
        report = {
            "frames": workbench.frames,
            "selections": {"perception": {
                "preset": validation.PERCEPTION_PRESET, "config": {"plugins": ["fixture"]},
            }},
        }
        with (
            patch.object(validation, "inspect_report", return_value=report),
            patch.object(validation, "serve", return_value=nullcontext(workbench)),
        ):
            return validation.check_parity()

    def test_parity_visits_every_frame_across_chunk_boundaries(self):
        for count in (1, 50, 51):
            with self.subTest(count=count):
                workbench = Workbench(count)
                self.assertEqual(self.check(workbench), [])
                self.assertEqual(workbench.seeks[-count:], list(range(count)))

    def test_parity_detects_identity_perception_memory_and_action_mismatches(self):
        def corrupt(state, position):
            if position == 1:
                state["current_frame"]["frame_id"] = "wrong"
                state["perception"]["status"] = "error"
                state["perception"]["plugin_runs"][0]["signal_count"] = 999
                state["cycle"]["action"] = None
                state["cycle"]["memory"] = None

        problems = self.check(Workbench(2, corrupt))
        for finding in ("frame identity", "status", "plugin runs", "no decision status", "no memory record count"):
            self.assertTrue(any(finding in problem for problem in problems), problems)
        self.assertFalse(any("parity check failed" in problem for problem in problems), problems)

    def test_absent_perception_does_not_skip_memory_and_action_checks(self):
        def corrupt(state, position):
            if position == 1:
                state["cycle"]["action"] = None

        workbench = Workbench(2, corrupt)
        workbench.frames[1]["record"] = None
        self.assertEqual(self.check(workbench), ["frame 1: no decision status"])

    def test_empty_inspection_is_reported_without_starting_a_viewer(self):
        self.assertEqual(self.check(Workbench(0)), ["inspect returned no frames"])
