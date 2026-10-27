from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.step_inspect import inspect_step
from cli.automa_cli.memory_report import plugin_summaries
from cli.automa_cli.workbench_runner import ImageReplayRunner
from tests.shared.cli.memory.frame_fixtures import write_frames


class WorkbenchAgreesWithInspectTests(unittest.TestCase):
    """The workbench shows what `plugins inspect memory` reports for the same frames."""

    def test_workbench_reads_a_memory_recordings_frame_identity_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"AUTOMA_MEMORY_INSPECT_ROOT": str(Path(directory) / "inspections")},
        ):
            root = Path(directory)
            frames = write_frames(root / "frames", count=2)
            (frames / "manifest.json").write_text(json.dumps({
                "frames": [
                    {
                        "frame_id": "capture-5", "frame_index": 5,
                        "image_path": "frame_0.png", "timestamp_ms": 5000,
                    },
                    {
                        "frame_id": "capture-8", "frame_index": 8,
                        "image_path": "frame_1.png", "timestamp_ms": 5000,
                    },
                ],
            }))
            result = inspect_step("memory", source=str(frames), record=True, json_output=True)
            self.assertEqual(result.exit_code, 0, result.message)
            inspected = json.loads(result.message)
            runner = ImageReplayRunner(inspected["run_dir"], cadence_ms=0, loop=False)
            self.addCleanup(runner.close)
            runner.start()
            completed = runner.wait(5)
        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(
            [
                (item["frame"]["frame_id"], item["frame"]["timestamp_ms"])
                for item in completed["timeline"]
            ],
            [(frame["frame_id"], frame["timestamp_ms"]) for frame in inspected["frames"]],
        )
        shown = json.loads(json.dumps(completed["steps"]["memory"]))
        final = inspected["frames"][-1]["record"]
        self.assertEqual(shown["evidence_publisher"], final["evidence_publisher"])
        self.assertEqual(
            [(item["plugin_id"], item["state"]) for item in shown["plugins"]],
            [(item["plugin_id"], item["state"]) for item in final["plugins"]],
        )

    def test_workbench_memory_equals_inspect_for_the_same_source_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"AUTOMA_MEMORY_INSPECT_ROOT": str(Path(directory) / "inspections")},
        ):
            frames = write_frames(Path(directory) / "frames", count=4)
            result = inspect_step("memory", source=str(frames), json_output=True)
            self.assertEqual(result.exit_code, 0, result.message)
            inspected = json.loads(result.message)

            runner = ImageReplayRunner(frames, cadence_ms=0, loop=False)
            runner.start()
            completed = runner.wait(10)

        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(
            completed["active_memory_plugin_ids"], inspected["selections"]["memory"]["plugins"]
        )
        self.assertEqual(
            [item["memory_plugins"] for item in completed["timeline"]],
            [plugin_summaries(frame["record"]) for frame in inspected["frames"]],
        )
        self.assertEqual(
            [item["memory_evidence_publisher"] for item in completed["timeline"]],
            [frame["record"]["evidence_publisher"] for frame in inspected["frames"]],
        )
        shown = plugin_summaries(completed["steps"]["memory"])
        self.assertEqual(shown, plugin_summaries(inspected["frames"][-1]["record"]))
        self.assertEqual(completed["summary"]["memory_plugins"], shown)
        self.assertEqual(
            completed["summary"]["memory_evidence_publisher"],
            inspected["frames"][-1]["record"]["evidence_publisher"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
