from __future__ import annotations

import json
import unittest

from tests.cli.memory.test_inspect import write_frames
from tests.cli.workbench_fixtures import (
    ImageReplayRunner,
    _wait_until,
    image_source,
    perception_activations,
    post_action,
    serve_workbench,
    write_manifest,
)
from tests.support.cli_runner import run_automa


class SelectionRebuildTests(unittest.TestCase):
    def test_cli_help_describes_selection_replay_for_both_steps(self) -> None:
        help_text = " ".join(
            run_automa("vehicles", "workbench", "replay", "--help").stdout.split()
        )
        self.assertIn("Changing perception or memory plugins", help_text)
        self.assertIn("rebuilds both pipelines", help_text)
        self.assertIn("displayed frame before returning", help_text)

    def test_either_step_replays_the_displayed_frame_between_loop_passes(self) -> None:
        for phase in ("running", "paused"):
            for step in ("perception", "memory"):
                with self.subTest(phase=phase, step=step), image_source(1) as root:
                    runner = ImageReplayRunner(
                        root,
                        activations=perception_activations("frame"),
                        cadence_ms=30000,
                        loop=True,
                    )
                    base = serve_workbench(self, runner)
                    if step == "memory":
                        post_action(
                            base,
                            {
                                "action": "select_plugins",
                                "step": "memory",
                                "active_plugin_ids": [],
                            },
                        )
                    started = post_action(base, {"action": "start"})["state"]
                    run_id = started["run_id"]
                    _wait_until(
                        lambda owner=runner: owner.state()["current_frame"] is not None
                    )
                    shown = runner.state()
                    if phase == "paused":
                        shown = post_action(
                            base,
                            {
                                "action": "pause",
                                "run_id": run_id,
                            },
                        )["state"]
                    # The next pass is at zero, but the previous frame is displayed.
                    self.assertEqual(shown["position"], 0)
                    self.assertEqual(shown["timeline"], [])
                    frame_id = shown["current_frame"]["frame_id"]
                    selected_ids = (
                        ["frame", "floor_plane"]
                        if step == "perception"
                        else ["bounded_evidence"]
                    )
                    selected = post_action(
                        base,
                        {
                            "action": "select_plugins",
                            "run_id": run_id,
                            "step": step,
                            "active_plugin_ids": selected_ids,
                        },
                    )["state"]

                    self.assertEqual(selected["phase"], phase)
                    self.assertEqual(selected["current_frame"]["frame_id"], frame_id)
                    self.assertIsNotNone(selected["steps"]["perception"])
                    self.assertIsNotNone(selected["steps"]["observation"])
                    self.assertIsNotNone(selected["steps"]["memory"])
                    self.assertIsNotNone(selected["steps"]["decision"])
                    self.assertEqual(
                        [
                            run["plugin_id"]
                            for run in selected["steps"]["perception"]["plugin_runs"]
                        ],
                        selected_ids
                        if step == "perception"
                        else shown["active_perception_plugin_ids"],
                    )
                    other = "memory" if step == "perception" else "perception"
                    self.assertEqual(
                        selected[f"active_{other}_plugin_ids"],
                        shown[f"active_{other}_plugin_ids"],
                    )
                    self.assertEqual(
                        selected["steps"]["memory"]["plugin_id"], "bounded_evidence"
                    )
                    if phase == "paused":
                        self.assertEqual(selected["position"], 1)
                        self.assertEqual(len(selected["timeline"]), 1)
                        self.assertEqual(runner._memory_step.update_count, 1)
                        unchanged_step = runner._perception_step
                        unchanged = post_action(
                            base,
                            {
                                "action": "select_plugins",
                                "run_id": run_id,
                                "step": step,
                                "active_plugin_ids": selected_ids,
                            },
                        )["state"]
                        self.assertIs(runner._perception_step, unchanged_step)
                        self.assertEqual(unchanged["position"], 1)
                    post_action(base, {"action": "reset", "run_id": run_id})

    def test_either_step_discards_future_tracking_and_retention_after_a_backward_seek(
        self,
    ) -> None:
        tracked = ["frame", "floor_plane", "multi_obstruction_tracks"]
        for step in ("perception", "memory"):
            with self.subTest(step=step), image_source(0) as root:
                write_frames(root, count=6)
                write_manifest(
                    root,
                    {
                        "source_id": "selection-fixture",
                        "frames": [
                            {
                                "frame_id": f"frame-{index}",
                                "image_path": f"frame_{index}.png",
                                "timestamp_ms": index * 1000,
                            }
                            for index in range(6)
                        ],
                    },
                )
                runner = ImageReplayRunner(
                    root,
                    activations=perception_activations(
                        *(tracked if step == "memory" else ["frame", "floor_plane"])
                    ),
                    cadence_ms=30000,
                )
                base = serve_workbench(self, runner)
                if step == "memory":
                    post_action(
                        base,
                        {
                            "action": "select_plugins",
                            "step": "memory",
                            "active_plugin_ids": [],
                        },
                    )
                run_id = post_action(base, {"action": "start"})["state"]["run_id"]
                _wait_until(lambda owner=runner: owner.state()["position"] == 1)
                post_action(base, {"action": "pause", "run_id": run_id})
                post_action(base, {"action": "seek", "run_id": run_id, "position": 4})
                shown = post_action(
                    base,
                    {
                        "action": "seek",
                        "run_id": run_id,
                        "position": 1,
                    },
                )["state"]
                self.assertEqual(len(shown["timeline"]), 5)
                runner._shared_memory["future-marker"] = True
                selected = post_action(
                    base,
                    {
                        "action": "select_plugins",
                        "run_id": run_id,
                        "step": step,
                        "active_plugin_ids": tracked
                        if step == "perception"
                        else ["bounded_evidence"],
                    },
                )["state"]
                self.assertEqual(selected["phase"], "paused")
                self.assertEqual(selected["position"], 2)
                self.assertEqual(len(selected["timeline"]), 2)
                self.assertNotIn("future-marker", runner._shared_memory)
                self.assertEqual(runner._memory_step.update_count, 2)

                reference = ImageReplayRunner(
                    root, activations=perception_activations(*tracked), cadence_ms=30000
                )
                self.addCleanup(reference.close)
                reference_id = reference.start()["run_id"]
                _wait_until(lambda owner=reference: owner.state()["position"] == 1)
                reference.dispatch("pause", run_id=reference_id)
                expected = reference.dispatch("seek", run_id=reference_id, position=1)
                for key in ("signals", "things", "status", "limits"):
                    self.assertEqual(
                        selected["steps"]["perception"][key],
                        json.loads(json.dumps(expected["steps"]["perception"][key])),
                        key,
                    )
                self.assertEqual(
                    selected["steps"]["memory"],
                    json.loads(json.dumps(expected["steps"]["memory"])),
                )
                # Observation prose includes execution timings. Compare the
                # structured evidence and every decision field without it.
                decision = selected["steps"]["decision"]
                reference_decision = json.loads(
                    json.dumps(expected["steps"]["decision"])
                )
                for payload in (decision, reference_decision):
                    payload["proposal"]["source"]["observation"]["value"].pop("summary")
                self.assertEqual(decision, reference_decision)
                post_action(base, {"action": "reset", "run_id": run_id})


if __name__ == "__main__":
    unittest.main()
