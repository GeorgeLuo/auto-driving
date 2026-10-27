"""Inspect and workbench apply the same recorded selections and overrides."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cli.automa_cli.inspection_runs import default_selection
from cli.automa_cli.step_inspect import inspect_step
from cli.automa_cli.workbench_runner import ImageReplayRunner
from tests.shared.cli.memory.frame_fixtures import write_frames


class ReplaySelectionTests(unittest.TestCase):
    def test_absent_empty_and_configured_memory_match_in_both_replay_callers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=3)
            configured = default_selection("memory")
            empty = replace(configured, plugins=())
            for selection in (None, empty, configured):
                with self.subTest(selection=selection):
                    (source / "manifest.json").write_text(json.dumps({"frames": [
                        {"image_path": f"frame_{i}.png", "steps": {"memory": selection.to_payload() if selection else None}}
                        for i in range(3)
                    ]}))
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
            (source / "manifest.json").write_text(json.dumps({"frames": [
                {"image_path": f"frame_{i}.png", "steps": {"memory": selection.to_payload() if selection else None}}
                for i, selection in enumerate((default, None, empty))
            ]}))
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
