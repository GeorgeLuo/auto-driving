"""Inspect and workbench apply the same recorded selections and overrides."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.perception.runner import PerceptionRunner
from cli.automa_cli.workbench.frames import run_frame
from cli.automa_cli.plugins.step_replay import read_replay_source

from cli.automa_cli.hosts.inspection_runs import default_selection
from cli.automa_cli.plugins.step_inspect import inspect_step
from cli.automa_cli.workbench.runner import ImageReplayRunner
from tests.shared.cli.memory.frame_fixtures import write_frames


class ReplaySelectionTests(unittest.TestCase):
    def test_absent_empty_and_configured_memory_match_in_both_replay_callers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=3)
            configured = default_selection("memory")
            empty = replace(configured, plugins=())
            for selection in (None, empty, configured):
                with self.subTest(selection=selection):
                    (source / "manifest.json").write_text(json.dumps({
                        "steps": {"memory": selection.to_payload() if selection else None},
                        "frames": [{"image_path": f"frame_{i}.png"} for i in range(3)],
                    }))
                    inspected = inspect_step("memory", source=source, json_output=True)
                    self.assertEqual(inspected.exit_code, 0, inspected.message)
                    expected = json.loads(inspected.message)["frames"][-1]["record"]
                    runner = ImageReplayRunner(source, cadence_ms=0, loop=False)
                    try:
                        runner.start()
                        completed = runner.wait(5)
                        self.assertEqual(completed["phase"], "completed", completed.get("failure"))
                        self.assertEqual(json.loads(json.dumps(completed["steps"]["memory"])), expected)
                    finally:
                        runner.close()
                    if selection is None:
                        self.assertIsNone(expected)
                    elif not selection.plugins:
                        self.assertEqual(expected["plugins"], [])
                    else:
                        self.assertGreater(expected["plugins"][0]["state"]["record_count"], 0)

    def test_recorded_restages_and_an_explicit_override_match_in_both_callers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=3)
            default = default_selection("memory")
            empty = replace(default, plugins=())
            (source / "manifest.json").write_text(json.dumps({
                "steps": {"memory": default.to_payload()},
                "frames": [
                    {"image_path": "frame_0.png"},
                    {"image_path": "frame_1.png", "steps": {"memory": None}},
                    {"image_path": "frame_2.png", "steps": {"memory": empty.to_payload()}},
                ],
            }))
            for overridden in (False, True):
                options = {"plugins": ["bounded_evidence"]} if overridden else {}
                inspected = inspect_step("memory", source=source, json_output=True, **options)
                self.assertEqual(inspected.exit_code, 0, inspected.message)
                expected = json.loads(inspected.message)
                runner = ImageReplayRunner(source, cadence_ms=0, loop=False,
                                           activations={"memory": default} if overridden else None)
                try:
                    runner.start()
                    completed = runner.wait(5)
                    self.assertEqual(completed["phase"], "completed", completed.get("failure"))
                    self.assertEqual(json.loads(json.dumps(completed["steps"]["memory"])), expected["frames"][-1]["record"])
                    self.assertEqual([row["memory_plugins"] for row in completed["timeline"]],
                                     [([] if frame["record"] is None else [
                                         {"plugin_id": item["plugin_id"], **{key: item["state"].get(key) for key in
                                          ("health", "record_count", "epoch_id")}}
                                         for item in frame["record"]["plugins"]]) for frame in expected["frames"]])
                finally:
                    runner.close()

    def test_top_level_legacy_config_does_not_select_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=1)
            # Old reports used this executable config; only steps now selects runners.
            (source / "report.json").write_text(json.dumps({
                "memory": {"preset": "custom", "config": {"plugins": [], "plugin_specs": {}}},
                "frames": [{"image_path": "frame_0.png"}],
            }))
            inspected = inspect_step("memory", source=source, json_output=True)
            self.assertEqual(inspected.exit_code, 0, inspected.message)
            expected = json.loads(inspected.message)["frames"][0]["record"]
            self.assertEqual([item["plugin_id"] for item in expected["plugins"]], ["bounded_evidence"])
            runner = ImageReplayRunner(source, cadence_ms=0, loop=False)
            try:
                runner.start()
                completed = runner.wait(5)
                self.assertEqual(completed["phase"], "completed", completed.get("failure"))
                self.assertEqual(json.loads(json.dumps(completed["steps"]["memory"])), expected)
            finally:
                runner.close()

    def test_replay_uses_normal_perception_runner_status_reset_and_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=1)
            images = read_replay_source(source, max_frames=512)
            frame = replace(images.frames[0], frame_id="frame-7", frame_index=7)
            mapper = PerceptionRunner.from_activation(default_selection("perception"))
            memory = {}
            requests = []
            perceive = mapper.perceive
            mapper.perceive = lambda request: requests.append(request) or perceive(request)
            result = run_frame(frame, steps={"perception": mapper}, shared_memory=memory)
            self.assertEqual(mapper.last_frame_index, 7)
            self.assertEqual(requests[0].metadata["source_id"], frame.source_id)
            self.assertEqual(requests[0].metadata["sequence_index"], frame.position)
            self.assertEqual(requests[0].metadata["frame_index"], frame.frame_index)
            self.assertIs(mapper.last_output, result.result.perception)
            timing = result.result.timing["steps"][0]
            self.assertEqual(timing["step"], "perception")
            self.assertEqual([item["plugin_id"] for item in timing["plugins"]], list(mapper.plugin_ids))
            with patch.object(mapper, "reset", wraps=mapper.reset) as reset:
                absent = run_frame(
                    replace(frame, frame_id="gap", frame_index=8, image_path=None,
                            absence_reason="camera missing"),
                    steps={"perception": mapper}, shared_memory=memory,
                )
                reset.assert_called_once_with(memory)
            self.assertIsNone(absent.result.perception)
            self.assertEqual(mapper.last_frame_index, 8)
