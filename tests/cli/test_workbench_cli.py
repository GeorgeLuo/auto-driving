from __future__ import annotations
import unittest
from tests.support.cli_runner import run_automa
from tests.cli.workbench_fixtures import image_source


class WorkbenchTests(unittest.TestCase):
    def test_cli_replay_human_output_names_recovery_and_cleanup(self) -> None:
        with image_source(1) as root:
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
