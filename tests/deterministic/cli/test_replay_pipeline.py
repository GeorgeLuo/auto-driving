"""Recorded transitions and overrides obey the same pipeline replacement rule."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from autonomy.decision_cycle.activation import STEPS, step_activation
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.shared_memory import shared_memory_document
from cli.automa_cli.plugins.inspect.replay import StepReplay, read_replay_source
from cli.automa_cli.replay.selections import default_selection, read_shared_memory_seed
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import _RecordingMemory
from tests.shared.cli.workbench_fixtures import ImageReplayRunner, image_source, write_manifest, _wait_until


class CountMemory(_RecordingMemory):
    def update(self, context):
        super().update(context)
        context.shared_memory["count"] = context.shared_memory.get("count", 0) + 1

    def status(self, shared_memory):
        return {**super().status(shared_memory), "count": shared_memory.get("count", 0)}


def recording(root):
    selected = {step: default_selection(step) for step in STEPS}
    selected.update(
        perception=packaged_activation("perception", ["frame"]),
        memory=step_activation("memory", ["recording_test"], {"recording_test": f"{__name__}:CountMemory"}),
        proposal=None, plan=None,
    )
    alternate = {**selected, "perception": replace(selected["perception"], plugins=())}
    write_manifest(root, {"frames": [
        {"frame_id": f"f-{index}", "image_path": f"frame_{index:02d}.png",
         "steps": {step: activation.to_payload() if activation else None for step, activation in definitions.items()}}
        for index, definitions in enumerate((selected, selected, alternate, alternate))
    ]})
    return read_replay_source(root, max_frames=4), selected


class ReplayPipelineTests(unittest.TestCase):
    def test_inspection_reconstructs_memory_and_does_not_reapply_the_startup_seed(self):
        with image_source(4) as root:
            source, _ = recording(root)
            seed_path = root / "seed.json"
            seed_path.write_text(json.dumps(shared_memory_document({"count": 40})))
            replay = StepReplay(source, steps=STEPS, seed=read_shared_memory_seed(seed_path))
            replay.run(source.frames[0])
            initial, memory = replay.runners, replay.shared_memory
            replay.run(source.frames[1])
            self.assertIs(replay.runners, initial)
            self.assertEqual(replay.shared_memory["count"], 42)
            replay.run(source.frames[2])
            self.assertIsNot(replay.runners, initial)
            self.assertIsNot(replay.runners["memory"], initial["memory"])
            self.assertIsNot(replay.shared_memory, memory)
            self.assertEqual(replay.shared_memory["count"], 1)

    def test_inspection_override_cancels_the_recorded_change_without_a_reset(self):
        with image_source(4) as root:
            source, selected = recording(root)
            replay = StepReplay(source, steps=STEPS, overrides={"perception": selected["perception"]})
            initial, memory = replay.runners, replay.shared_memory
            for frame in source.frames[:3]:
                replay.run(frame)
            self.assertIs(replay.runners, initial)
            self.assertIs(replay.shared_memory, memory)
            self.assertEqual(replay.shared_memory["count"], 3)

    def test_inspection_failed_transition_keeps_the_previous_pipeline_and_map(self):
        with image_source(4) as root:
            source, _ = recording(root)
            replay = StepReplay(source, steps=STEPS)
            replay.run(source.frames[0])
            initial, memory, activations = replay.runners, replay.shared_memory, replay.activations
            with patch.object(MemoryRunner, "validate_selection", side_effect=RuntimeError("build refused")):
                with self.assertRaisesRegex(RuntimeError, "build refused"):
                    replay.restage(source.frames[2])
            self.assertIs(replay.runners, initial)
            self.assertIs(replay.shared_memory, memory)
            self.assertIs(replay.activations, activations)
            self.assertEqual(replay.shared_memory["count"], 1)

    def test_workbench_reconstructs_on_recorded_changes_but_not_cancelled_overrides(self):
        for override in (False, True):
            with self.subTest(override=override), image_source(4) as root:
                _, selected = recording(root)
                runner = ImageReplayRunner(root, cadence_ms=30000,
                                           activations={"perception": selected["perception"]} if override else None)
                self.addCleanup(runner.close)
                run_id = runner.start()["run_id"]
                _wait_until(lambda: runner.state()["position"] == 1)
                first = runner.dispatch("pause", run_id=run_id)
                initial, memory = runner._steps, runner._shared_memory
                runner.dispatch("step", run_id=run_id)
                self.assertIs(runner._steps, initial)
                runner.dispatch("step", run_id=run_id)
                if override:
                    self.assertIs(runner._steps, initial)
                    self.assertIs(runner._shared_memory, memory)
                    self.assertEqual(memory["count"], 3)
                else:
                    self.assertIsNot(runner._steps["memory"], initial["memory"])
                    self.assertIsNot(runner._shared_memory, memory)
                    self.assertEqual(runner._shared_memory["count"], 1)
                previous = runner.dispatch("seek", run_id=run_id, position=0)
                self.assertEqual(previous["cycle"], first["cycle"])

    def test_workbench_failed_recorded_transition_keeps_the_last_output_and_map(self):
        with image_source(4) as root:
            recording(root)
            runner = ImageReplayRunner(root, cadence_ms=30000)
            self.addCleanup(runner.close)
            run_id = runner.start()["run_id"]
            _wait_until(lambda: runner.state()["position"] == 1)
            runner.dispatch("pause", run_id=run_id)
            previous = runner.dispatch("step", run_id=run_id)
            initial, memory = runner._steps, runner._shared_memory
            with patch.object(MemoryRunner, "validate_selection", side_effect=RuntimeError("build refused")):
                failed = runner.dispatch("step", run_id=run_id)
            self.assertEqual(failed["phase"], "failed")
            self.assertIs(runner._steps, initial)
            self.assertIs(runner._shared_memory, memory)
            self.assertEqual(memory["count"], 2)
            self.assertEqual(failed["cycle"], previous["cycle"])
            self.assertEqual(failed["current_frame"], previous["current_frame"])
