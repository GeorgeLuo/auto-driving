from __future__ import annotations
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.support.cli_runner import run_automa
from tests.cli.workbench_fixtures import (
    _make_images,
)


class WorkbenchTests(unittest.TestCase):
    def test_cli_replay_machine_readable_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            result = run_automa(
                "vehicles",
                "workbench",
                "replay",
                str(root),
                "--cadence-ms",
                "0",
                "--json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["phase"], "completed")
        self.assertEqual(payload["sequence_id"], "workbench.image_replay.v1")
        self.assertEqual(
            payload["machine_detail"]["pipeline"]["perception_algorithm"],
            "lightweight_observer",
        )
        self.assertEqual(
            payload["decision"]["frame_id"],
            payload["current_frame"]["frame_id"],
        )
        self.assertFalse(payload["decision"]["authority"]["proposed_applied"])
        self.assertNotIn("argv", payload)

    def test_cli_replay_accepts_realtime_pace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            result = run_automa(
                "vehicles",
                "workbench",
                "replay",
                str(root),
                "--pace",
                "realtime",
                "--cadence-ms",
                "0",
                "--json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["phase"], "completed")
        self.assertEqual(payload["controls"]["pace"], "realtime")

    def test_cli_replay_human_output_names_recovery_and_cleanup(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            result = run_automa(
                "vehicles",
                "workbench",
                "replay",
                str(root),
                "--cadence-ms",
                "0",
            )

        self.assertIn("phase: completed", result.stdout)
        self.assertIn("recovery:", result.stdout)
        self.assertIn("cleanup:", result.stdout)
        self.assertIn("source_read_only=True", result.stdout)
