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
