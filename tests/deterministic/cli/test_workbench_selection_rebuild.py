from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from autonomy.runtime.plugin_loader import StagedStep
from cli.automa_cli.workbench import runner as replay
from cli.automa_cli.frontend.workbench_contract import ReplayActionError
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.releases.paths import controller_bundle_paths
from implementations.decision_cycle.catalog import selection_activation
from tests.shared.cli.workbench_fixtures import ImageReplayRunner, image_source, write_manifest, _wait_until


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

    def test_bundled_selection_reconstructs_every_step_and_loads_additions_in_bundle(self):
        with tempfile.TemporaryDirectory() as directory, image_source(3) as root:
            bundle = controller_bundle_paths(Path(directory) / "vehicle")
            sync_controller_bundle(bundle, emit=None)
            code = Path(bundle["root_dir"])
            (code / "implementations/recorded_frame.py").write_text(
                "from implementations.decision_cycle.perception.plugins.frame.plugin import FrameObservationPlugin\n"
                "class RecordedFrame(FrameObservationPlugin):\n"
                "    def __init__(self):\n"
                "        self.reset_count = 0\n"
                "    def reset(self, shared_memory=None):\n"
                "        from implementations.recorded_frame import RESET_KEY\n"
                "        self.reset_count += 1\n"
                "        shared_memory.pop(RESET_KEY, None)\n"
                "RESET_KEY = 'recorded.frame'\n",
                encoding="utf-8",
            )
            activation = replace(
                selection_activation("perception", plugins=["frame"]),
                plugin_specs={"frame": "implementations.recorded_frame:RecordedFrame"},
                metadata={"controller_bundle": {"root_dir": str(code)}},
            )
            write_manifest(root, {"frames": [
                {"frame_id": f"frame-{index}", "image_path": f"frame_{index:02d}.png",
                 "steps": {"perception": activation.to_payload()}}
                for index in range(3)
            ]})
            runner, run_id = self.paused(root)
            step = runner._steps["perception"]
            self.assertIsInstance(step, StagedStep)
            plugin = step.plugins["frame"]
            self.select(runner, run_id, ["frame", "floor_plane"])
            self.assertIsNot(runner._steps["perception"], step)
            self.assertIsNot(runner._steps["perception"].plugins["frame"], plugin)
            step = runner._steps["perception"]
            self.assertEqual(plugin.reset_count, 0)
            self.assertEqual(step.plugin_ids, ("frame", "floor_plane"))
            self.assertTrue(Path(step.plugins["floor_plane"].perceive.__code__.co_filename).is_relative_to(code))
            # The discarded map and its checkpoint cannot restore old plugin state.
            runner._shared_memory["recorded.frame"] = True
            runner._frame_checkpoint["memory"]["recorded.frame"] = True
            self.select(runner, run_id, ["floor_plane"])
            self.assertNotIn("recorded.frame", runner._shared_memory)
            self.assertNotIn("recorded.frame", runner._frame_checkpoint["memory"])
            self.assertEqual(plugin.reset_count, 0)

    def test_running_addition_reconstructs_plugins_with_tuned_configs_and_empty_history(self):
        activation = selection_activation("perception", plugins=["motion_tracks"])
        activation = replace(activation, plugin_configs={"motion_tracks": {"max_features": 12}})
        with image_source(6) as root:
            runner, run_id = self.paused(root, activations={"perception": activation})
            runner.dispatch("seek", run_id=run_id, position=3)
            step = runner._steps["perception"]
            plugin = step.plugins["motion_tracks"]
            self.assertIn("perception.motion_tracks.history", runner._shared_memory)
            memory = runner._steps["memory"]
            # Keep the worker asleep while exercising the running action boundary.
            with runner._action_lock:
                runner.dispatch("resume", run_id=run_id)
                with mock.patch.object(replay, "run_frame", wraps=replay.run_frame) as frames:
                    selected = self.select(runner, run_id, ["motion_tracks", "frame"])
                    frames.assert_not_called()
                self.assertEqual(selected["position"], 4)
                self.assertIsNot(runner._steps["perception"], step)
                self.assertIsNot(runner._steps["perception"].plugins["motion_tracks"], plugin)
                self.assertEqual(runner._steps["perception"].plugins["motion_tracks"].max_features, 12)
                self.assertEqual(runner._shared_memory, {})
                self.assertIsNot(runner._steps["memory"], memory)
                runner.dispatch("pause", run_id=run_id)
            next_frame = runner.dispatch("step", run_id=run_id)
            self.assertEqual([p["plugin_id"] for p in next_frame["perception"]["plugin_runs"]],
                             ["motion_tracks", "frame"])

    def test_paused_refresh_executes_only_latest_frame_without_advancing_memory_twice(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            runner.dispatch("seek", run_id=run_id, position=3)
            prefix = [runner.dispatch("seek", run_id=run_id, position=index) for index in range(3)]
            runner.dispatch("seek", run_id=run_id, position=3)
            memory = runner._steps["memory"]
            with mock.patch.object(replay, "run_frame", wraps=replay.run_frame) as frames:
                self.select(runner, run_id, ["frame"])
                refreshed = self.select(runner, run_id, ["frame", "floor_plane"])
                self.assertEqual([c.args[0].position for c in frames.call_args_list], [3, 3])
                self.select(runner, run_id, ["frame", "floor_plane"])
                self.assertEqual(frames.call_count, 2)
            self.assertIsNot(runner._steps["memory"], memory)
            self.assertEqual(refreshed["phase"], "paused")
            self.assertEqual(refreshed["position"], 4)
            for index, earlier in enumerate(prefix):
                cached = runner.dispatch("seek", run_id=run_id, position=index)
                self.assertEqual((cached["cycle"], cached["perception"]), (earlier["cycle"], earlier["perception"]))
                self.assertEqual(cached["timing"], earlier["timing"])
                self.assertEqual(cached["machine_detail"]["pipeline"], earlier["machine_detail"]["pipeline"])
            self.assertEqual(refreshed["progress"]["completed"], 4)
            # Domain memory matches one execution of the frame with this selection.
            self.assertEqual(runner._steps["memory"].update_count, 1)
            self.assertEqual(refreshed["cycle"]["memory"]["evidence_publisher"], "bounded_evidence")

    def test_new_motion_plugin_warms_up_and_reenabled_plugin_does_not_restore_old_history(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            runner.dispatch("seek", run_id=run_id, position=2)
            selected = self.select(runner, run_id, ["frame", "motion_tracks"])
            motion = selected["perception"]["plugin_runs"][-1]
            self.assertEqual(motion["status"], "warming_up")
            runner.dispatch("step", run_id=run_id)
            self.select(runner, run_id, ["frame"])
            self.assertNotIn("perception.motion_tracks.history", runner._shared_memory)
            selected = self.select(runner, run_id, ["frame", "motion_tracks"])
            self.assertEqual(selected["perception"]["plugin_runs"][-1]["status"], "warming_up")

    def test_historical_results_keep_their_selection_and_edits_require_latest_frame(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            runner.dispatch("seek", run_id=run_id, position=3)
            self.select(runner, run_id, ["frame"])
            old = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(old["active_perception_plugin_ids"], ["frame"])
            self.assertEqual(old["latest_processed_position"], 3)
            self.assertNotIn("select_plugins", old["controls"]["allowed_actions"])
            self.assertEqual([p["plugin_id"] for p in old["perception"]["plugin_runs"]],
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
            self.assertEqual(runner.state()["progress"]["completed"], 1)
            runner.dispatch("reset", run_id=run_id)
            self.assertIsNone(runner._frame_checkpoint)
            self.assertIsNone(runner.state()["latest_processed_position"])


if __name__ == "__main__":
    unittest.main()
