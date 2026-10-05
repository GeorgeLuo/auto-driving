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
        self.assertIn("cleanup: perception=reset; memory=reset;", result.stdout)
        self.assertIn("source_read_only=True", result.stdout)
        # Each step's selection, as the inspect commands print it.
        self.assertIn("perception: lightweight_observer (frame, floor_plane)", result.stdout)
        self.assertIn("memory: recency_ledger (bounded_evidence)", result.stdout)

    def test_cli_replay_takes_each_steps_preset_or_plugins(self) -> None:
        with image_source(1) as root:
            preset = run_automa(
                "vehicles", "workbench", "replay", str(root), "--cadence-ms", "0",
                "--perception-preset", "obstruction_observer",
                "--memory-plugin", "bounded_evidence",
            )
            plugins = run_automa(
                "vehicles", "workbench", "replay", str(root), "--cadence-ms", "0",
                "--perception-plugin", "floor_plane", "--perception-plugin", "frame",
                "--memory-preset", "recency_ledger",
            )
        self.assertIn(
            "perception: obstruction_observer (frame, floor_plane, multi_obstruction_tracks)",
            preset.stdout,
        )
        self.assertIn("memory: recency_ledger (bounded_evidence)", preset.stdout)
        self.assertIn("perception: custom (floor_plane, frame)", plugins.stdout)
        self.assertIn("memory: recency_ledger (bounded_evidence)", plugins.stdout)

    def test_cli_replay_rejects_mixed_removed_or_unknown_selections(self) -> None:
        with image_source(1) as root:
            for args, message in (
                (("--perception-preset", "sim_debug", "--perception-plugin", "frame"),
                 "not allowed with argument --perception-preset"),
                (("--memory-preset", "recency_ledger", "--memory-plugin", "bounded_evidence"),
                 "not allowed with argument --memory-preset"),
                (("--plugin", "frame"), "unrecognized arguments: --plugin"),
                (("--memory-plugin", "missing"), "unknown memory plugin(s) missing"),
            ):
                result = run_automa(
                    "vehicles", "workbench", "replay", str(root), *args, check=False
                )
                self.assertEqual(result.returncode, 2, args)
                self.assertIn(message, result.stdout + result.stderr, args)
