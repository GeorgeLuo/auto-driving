from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from unittest import mock

from cli.automa_cli.workbench import runner as replay
from cli.automa_cli.frontend.workbench_contract import ReplayActionError
from implementations.decision_cycle.catalog import selection_activation
from tests.shared.cli.workbench_fixtures import ImageReplayRunner, image_source, _wait_until


class LiveSelectionTests(unittest.TestCase):
    def paused(self, root, *, loop=False, activations=None):
        runner = ImageReplayRunner(root, cadence_ms=30000, loop=loop, activations=activations)
        self.addCleanup(runner.close)
        run_id = runner.start()["run_id"]
        _wait_until(lambda: runner.state()["current_frame"] is not None)
        runner.dispatch("pause", run_id=run_id)
        return runner, run_id

    def select(self, runner, run_id, plugins, step="perception"):
        return runner.dispatch("select_plugins", run_id=run_id, step=step, active_plugin_ids=plugins)

    def test_running_addition_keeps_history_instances_and_tuned_configs(self):
        activation = selection_activation("perception", plugins=["motion_tracks"])
        activation = replace(activation, plugin_configs={"motion_tracks": {"max_features": 12}})
        with image_source(6) as root:
            runner, run_id = self.paused(root, activations={"perception": activation})
            runner.dispatch("seek", run_id=run_id, position=3)
            step = runner._steps["perception"]
            plugin = step.plugins["motion_tracks"]
            history = runner._shared_memory["perception.motion_tracks.history"]
            memory = runner._steps["memory"]
            # Keep the worker asleep while exercising the running action boundary.
            with runner._action_lock:
                runner.dispatch("resume", run_id=run_id)
                with mock.patch.object(replay, "run_frame", wraps=replay.run_frame) as frames:
                    selected = self.select(runner, run_id, ["motion_tracks", "frame"])
                    frames.assert_not_called()
                self.assertEqual(selected["position"], 4)
                self.assertIs(step.plugins["motion_tracks"], plugin)
                self.assertEqual(plugin.max_features, 12)
                self.assertIs(runner._shared_memory["perception.motion_tracks.history"], history)
                self.assertIs(runner._steps["memory"], memory)
                runner.dispatch("pause", run_id=run_id)
            next_frame = runner.dispatch("step", run_id=run_id)
            self.assertEqual([p["plugin_id"] for p in next_frame["steps"]["perception"]["plugin_runs"]],
                             ["motion_tracks", "frame"])

    def test_paused_refresh_executes_only_latest_frame_without_advancing_memory_twice(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            before = runner.dispatch("seek", run_id=run_id, position=3)
            prefix = copy.deepcopy(before["timeline"][:3])
            memory = runner._steps["memory"]
            with mock.patch.object(replay, "run_frame", wraps=replay.run_frame) as frames:
                self.select(runner, run_id, ["frame"])
                refreshed = self.select(runner, run_id, ["frame", "floor_plane"])
                self.assertEqual([c.args[0].position for c in frames.call_args_list], [3, 3])
                self.select(runner, run_id, ["frame", "floor_plane"])
                self.assertEqual(frames.call_count, 2)
            self.assertIs(runner._steps["memory"], memory)
            self.assertEqual(refreshed["phase"], "paused")
            self.assertEqual(refreshed["position"], 4)
            self.assertEqual(refreshed["timeline"][:3], prefix)
            self.assertEqual(len(refreshed["timeline"]), 4)
            # Domain memory matches one execution of the frame with this selection.
            self.assertEqual(refreshed["steps"]["memory"], before["steps"]["memory"])
            self.assertEqual(refreshed["steps"]["memory"]["evidence_publisher"], "bounded_evidence")

    def test_new_motion_plugin_warms_up_and_reenabled_plugin_does_not_restore_old_history(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            runner.dispatch("seek", run_id=run_id, position=2)
            selected = self.select(runner, run_id, ["frame", "motion_tracks"])
            motion = selected["steps"]["perception"]["plugin_runs"][-1]
            self.assertEqual(motion["status"], "warming_up")
            runner.dispatch("step", run_id=run_id)
            self.select(runner, run_id, ["frame"])
            self.assertNotIn("perception.motion_tracks.history", runner._shared_memory)
            selected = self.select(runner, run_id, ["frame", "motion_tracks"])
            self.assertEqual(selected["steps"]["perception"]["plugin_runs"][-1]["status"], "warming_up")

    def test_historical_results_keep_their_selection_and_edits_require_latest_frame(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            runner.dispatch("seek", run_id=run_id, position=3)
            self.select(runner, run_id, ["frame"])
            old = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(old["active_perception_plugin_ids"], ["frame"])
            self.assertEqual(old["latest_processed_position"], 3)
            self.assertNotIn("select_plugins", old["controls"]["allowed_actions"])
            self.assertEqual([p["plugin_id"] for p in old["steps"]["perception"]["plugin_runs"]],
                             ["frame", "floor_plane"])
            with self.assertRaisesRegex(ReplayActionError, "latest processed frame"):
                self.select(runner, run_id, [])
            latest = runner.dispatch("seek", run_id=run_id, position=3)
            self.assertEqual(latest["phase"], "paused")
            self.assertIn("select_plugins", latest["controls"]["allowed_actions"])
            self.select(runner, run_id, [])

    def test_last_frame_remains_refreshable_before_next_loop(self):
        with image_source(1) as root:
            runner, run_id = self.paused(root, loop=True)
            with mock.patch.object(replay, "run_frame", wraps=replay.run_frame) as frames:
                selected = self.select(runner, run_id, ["frame"])
                self.assertEqual([c.args[0].position for c in frames.call_args_list], [0])
            self.assertEqual(selected["position"], 1)
            self.assertEqual(selected["latest_processed_position"], 0)
            runner.dispatch("step", run_id=run_id)
            self.assertEqual(len(runner.state()["timeline"]), 1)
            runner.dispatch("reset", run_id=run_id)
            self.assertIsNone(runner._frame_checkpoint)
            self.assertIsNone(runner.state()["latest_processed_position"])


if __name__ == "__main__":
    unittest.main()
