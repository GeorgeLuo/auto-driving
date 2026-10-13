from __future__ import annotations
import unittest
from tests.support.cli_runner import run_automa
from tests.shared.cli.workbench_fixtures import image_source


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

    def test_cli_replay_preset_and_plugin_flags_select_equivalent_recipes_for_each_step(self) -> None:
        cases = (
            ("perception", "lightweight_observer", ("frame", "floor_plane")),
            ("memory", "recency_ledger", ("bounded_evidence",)),
            ("proposal", "obstruction_avoidance", ("avoid_recent_obstruction",)),
        )

        def selections(output):
            return {step: next(line for line in output.splitlines() if line.startswith(f"{step}: "))
                    for step, _, _ in cases}

        with image_source(1) as root:
            command = ("vehicles", "workbench", "replay", str(root), "--cadence-ms", "0")
            implicit = selections(run_automa(*command).stdout)
            for step, preset, plugins in cases:
                with self.subTest(step=step):
                    named = run_automa(*command, f"--{step}-preset", preset)
                    flags = tuple(arg for plugin in plugins for arg in (f"--{step}-plugin", plugin))
                    selected = run_automa(*command, *flags)
                    # Both explicit inputs match the default, including the
                    # other steps that this request did not select.
                    self.assertEqual(selections(named.stdout), implicit)
                    self.assertEqual(selections(selected.stdout), implicit)
            # A non-default preset reaches its step alone; perception is the
            # step with more than one preset.
            chosen = selections(run_automa(*command, "--perception-preset", "sim_debug").stdout)
            self.assertEqual(chosen, {**implicit, "perception": "perception: sim_debug (frame, sim_color_targets)"})

    def test_cli_replay_rejects_mixed_removed_or_unknown_selections(self) -> None:
        with image_source(1) as root:
            for args, message in (
                (("--perception-preset", "sim_debug", "--perception-plugin", "frame"),
                 "not allowed with argument --perception-preset"),
                (("--memory-preset", "recency_ledger", "--memory-plugin", "bounded_evidence"),
                 "not allowed with argument --memory-preset"),
                (("--proposal-preset", "obstruction_avoidance", "--proposal-plugin", "avoid_recent_obstruction"),
                 "not allowed with argument --proposal-preset"),
                (("--proposal-preset", "missing"), "invalid choice: 'missing'"),
                (("--plugin", "frame"), "unrecognized arguments: --plugin"),
                (("--memory-plugin", "missing"), "unknown memory plugin(s) missing"),
            ):
                result = run_automa(
                    "vehicles", "workbench", "replay", str(root), *args, check=False
                )
                self.assertEqual(result.returncode, 2, args)
                self.assertIn(message, result.stdout + result.stderr, args)
