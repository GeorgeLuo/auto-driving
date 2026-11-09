"""A workbench selection rebuilds the replay's steps on a fresh map.

Selections use test plugins added to the workbench's catalog, so these tests
hold whatever the packaged catalog offers.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch, PerceptionSignal
from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract, PerceptionPluginWarmingUp
from autonomy.plugins import PluginDefinition
from autonomy.runtime.plugin_loader import StagedStep
from cli.automa_cli.workbench import runner as replay
from cli.automa_cli.frontend.workbench_contract import ReplayActionError
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.releases.paths import controller_bundle_paths
from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import RECORDING_SPEC
from tests.shared.cli.workbench_fixtures import ImageReplayRunner, image_source, write_manifest, _wait_until
from tests.support.perception_fixtures import LOCAL_PERCEPTION_SPEC

HISTORY_SPEC = "tests.deterministic.cli.test_workbench_selection_rebuild:_HistoryPerception"
HISTORY_KEY = "perception.history.frames"


class _HistoryPerception:
    """Keeps the frames it saw in the map, and warms up until it has seen ``window`` of them."""

    plugin_id = "history"
    contract = PerceptionPluginContract(state_mode="windowed", memory_required=True)

    def __init__(self, *, window: int) -> None:
        self.window = window

    def reset(self, shared_memory=None) -> None:
        if shared_memory is not None:
            shared_memory.pop(HISTORY_KEY, None)

    def perceive(self, inputs) -> PerceptionEvidenceBatch:
        frames = [*inputs.shared_memory.get(HISTORY_KEY, ()), inputs.frame_id]
        inputs.shared_memory[HISTORY_KEY] = frames
        if len(frames) < self.window:
            raise PerceptionPluginWarmingUp(f"{len(frames)} of {self.window} frames")
        return PerceptionEvidenceBatch(signals=(PerceptionSignal("history_frames", len(frames)),))


_DEFINITIONS = (
    PluginDefinition("perception", "a", LOCAL_PERCEPTION_SPEC, {"plugin_id": "a"}),
    PluginDefinition("perception", "b", LOCAL_PERCEPTION_SPEC, {"plugin_id": "b"}),
    PluginDefinition("perception", "history", HISTORY_SPEC, {"window": 2}),
    PluginDefinition("memory", "recording_test", RECORDING_SPEC),
)


def _runs(state: dict) -> list[str]:
    return [run["plugin_id"] for run in state["perception"]["plugin_runs"]]


class LiveSelectionTests(unittest.TestCase):
    def paused(self, root, *, loop=False, plugins=("a", "b"), activations=None, definitions=()):
        """A paused replay over the test catalog; ``plugins`` is perception's selection, if any."""

        runner = ImageReplayRunner(root, cadence_ms=30000, loop=loop, activations=activations)
        self.addCleanup(runner.close)
        runner.catalog.include((*_DEFINITIONS, *definitions))
        runner.dispatch("select_plugins", step="memory", active_plugin_ids=["recording_test"])
        if plugins is not None:
            runner.dispatch("select_plugins", step="perception", active_plugin_ids=list(plugins))
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
                "from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch\n"
                "from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract\n"
                "RESET_KEY = 'recorded.frame'\n"
                "class RecordedFrame:\n"
                "    contract = PerceptionPluginContract()\n"
                "    def __init__(self, *, plugin_id='recorded'):\n"
                "        self.plugin_id = plugin_id\n"
                "        self.reset_count = 0\n"
                "    def reset(self, shared_memory=None):\n"
                "        self.reset_count += 1\n"
                "        shared_memory.pop(RESET_KEY, None)\n"
                "    def perceive(self, inputs):\n"
                "        return PerceptionEvidenceBatch()\n",
                encoding="utf-8",
            )
            spec = "implementations.recorded_frame:RecordedFrame"
            activation = step_activation(
                "perception", ["recorded"], {"recorded": spec},
                metadata={"controller_bundle": {"root_dir": str(code)}},
            )
            write_manifest(root, {"frames": [
                {"frame_id": f"frame-{index}", "image_path": f"frame_{index:02d}.png",
                 "steps": {"perception": activation.to_payload()}}
                for index in range(3)
            ]})
            runner, run_id = self.paused(root, plugins=None, definitions=(
                PluginDefinition("perception", "recorded", spec),
                PluginDefinition("perception", "second", spec, {"plugin_id": "second"}),
            ))
            step = runner._steps["perception"]
            self.assertIsInstance(step, StagedStep)
            plugin = step.plugins["recorded"]
            self.select(runner, run_id, ["recorded", "second"])
            self.assertIsNot(runner._steps["perception"], step)
            self.assertIsNot(runner._steps["perception"].plugins["recorded"], plugin)
            step = runner._steps["perception"]
            self.assertEqual(plugin.reset_count, 0)
            self.assertEqual(step.plugin_ids, ("recorded", "second"))
            self.assertTrue(Path(step.plugins["second"].perceive.__code__.co_filename).is_relative_to(code))
            # Replacing the map cannot restore old plugin state.
            runner._shared_memory["recorded.frame"] = True
            self.select(runner, run_id, ["second"])
            self.assertNotIn("recorded.frame", runner._shared_memory)
            self.assertEqual(plugin.reset_count, 0)

    def test_running_addition_reconstructs_plugins_with_tuned_configs_and_empty_history(self):
        tuned = step_activation("perception", ["history"], {"history": HISTORY_SPEC}, {"history": {"window": 3}})
        with image_source(6) as root:
            runner, run_id = self.paused(root, plugins=None, activations={"perception": tuned})
            runner.dispatch("seek", run_id=run_id, position=3)
            step = runner._steps["perception"]
            plugin = step.plugins["history"]
            self.assertIn(HISTORY_KEY, runner._shared_memory)
            memory = runner._steps["memory"]
            # Keep the worker asleep while exercising the running action boundary.
            with runner._action_lock:
                runner.dispatch("resume", run_id=run_id)
                with mock.patch.object(replay, "run_frame", wraps=replay.run_frame) as frames:
                    selected = self.select(runner, run_id, ["history", "a"])
                    frames.assert_not_called()
                self.assertEqual(selected["position"], 4)
                self.assertIsNot(runner._steps["perception"], step)
                self.assertIsNot(runner._steps["perception"].plugins["history"], plugin)
                # The running selection's config, not the catalog's.
                self.assertEqual(runner._steps["perception"].plugins["history"].window, 3)
                self.assertEqual(runner._shared_memory, {})
                self.assertIsNot(runner._steps["memory"], memory)
                runner.dispatch("pause", run_id=run_id)
            next_frame = runner.dispatch("step", run_id=run_id)
            self.assertEqual(_runs(next_frame), ["history", "a"])

    def test_paused_refresh_executes_only_latest_frame_without_advancing_memory_twice(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            runner.dispatch("seek", run_id=run_id, position=3)
            prefix = [runner.dispatch("seek", run_id=run_id, position=index) for index in range(3)]
            runner.dispatch("seek", run_id=run_id, position=3)
            memory = runner._steps["memory"]
            with mock.patch.object(replay, "run_frame", wraps=replay.run_frame) as frames:
                self.select(runner, run_id, ["a"])
                refreshed = self.select(runner, run_id, ["a", "b"])
                self.assertEqual([c.args[0].position for c in frames.call_args_list], [3, 3])
                self.select(runner, run_id, ["a", "b"])
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

    def test_new_history_plugin_warms_up_and_reenabled_plugin_does_not_restore_old_history(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            runner.dispatch("seek", run_id=run_id, position=2)
            selected = self.select(runner, run_id, ["a", "history"])
            self.assertEqual(selected["perception"]["plugin_runs"][-1]["status"], "warming_up")
            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["perception"]["plugin_runs"][-1]["status"], "ok")
            self.select(runner, run_id, ["a"])
            self.assertNotIn(HISTORY_KEY, runner._shared_memory)
            selected = self.select(runner, run_id, ["a", "history"])
            self.assertEqual(selected["perception"]["plugin_runs"][-1]["status"], "warming_up")

    def test_historical_results_keep_their_selection_and_edits_require_latest_frame(self):
        with image_source(6) as root:
            runner, run_id = self.paused(root)
            runner.dispatch("seek", run_id=run_id, position=3)
            self.select(runner, run_id, ["a"])
            old = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(old["active_perception_plugin_ids"], ["a"])
            self.assertEqual(old["latest_processed_position"], 3)
            self.assertNotIn("select_plugins", old["controls"]["allowed_actions"])
            self.assertEqual(_runs(old), ["a", "b"])
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
                selected = self.select(runner, run_id, ["a"])
                self.assertEqual([c.args[0].position for c in frames.call_args_list], [0])
            self.assertEqual(selected["position"], 1)
            self.assertEqual(selected["latest_processed_position"], 0)
            runner.dispatch("step", run_id=run_id)
            self.assertEqual(runner.state()["progress"]["completed"], 1)
            runner.dispatch("reset", run_id=run_id)
            self.assertEqual(runner._shared_memory, {})
            self.assertIsNone(runner.state()["latest_processed_position"])


if __name__ == "__main__":
    unittest.main()
