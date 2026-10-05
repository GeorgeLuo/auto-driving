from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli import memory
from cli.automa_cli.memory import inspect_memory
from cli.automa_cli.memory_report import memory_summary
from cli.automa_cli.workbench_runner import ImageReplayRunner
from tests.cli.memory.test_inspect import write_frames


class WorkbenchAgreesWithInspectTests(unittest.TestCase):
    """The workbench shows what `memory inspect` reports for the same frames."""

    def test_workbench_reads_a_memory_recordings_frame_identity_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            memory, "INSPECT_ROOT", Path(directory) / "inspections"
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
            result = inspect_memory(str(frames), record=True, json_output=True)
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
        self.assertEqual(
            json.loads(json.dumps(completed["steps"]["memory"])),
            inspected["final"]["plugins"][0]["state"],
        )

    def test_workbench_memory_equals_inspect_for_the_same_source_and_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            memory, "INSPECT_ROOT", Path(directory) / "inspections"
        ):
            frames = write_frames(Path(directory) / "frames", count=4)
            result = inspect_memory(str(frames), json_output=True)
            self.assertEqual(result.exit_code, 0, result.message)
            inspected = json.loads(result.message)

            runner = ImageReplayRunner(frames, cadence_ms=0, loop=False)
            runner.start()
            completed = runner.wait(10)

        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(
            completed["active_memory_plugin_ids"], inspected["memory"]["plugins"]
        )
        last_plugin = [frame["plugins"][-1] for frame in inspected["frames"]]
        self.assertEqual(
            [item["memory_record_count"] for item in completed["timeline"]],
            [plugin["record_count"] for plugin in last_plugin],
        )
        shown = memory_summary(completed["steps"]["memory"])
        expected = {key: last_plugin[-1][key] for key in ("health", "record_count", "epoch_id")}
        self.assertEqual(shown, expected)
        self.assertEqual(completed["summary"]["memory_health"], expected["health"])
        self.assertEqual(completed["summary"]["memory_records"], expected["record_count"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
