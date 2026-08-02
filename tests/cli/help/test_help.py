from __future__ import annotations

import shlex
import unittest
from pathlib import Path

from cli.automa_cli.app import build_parser
from tests.support.cli_runner import run_automa


ROOT = Path(__file__).resolve().parents[3]


class HelpCommandTests(unittest.TestCase):
    def test_top_level_help_explains_purpose_without_nested_usage(self) -> None:
        result = run_automa("help")

        self.assertIn("Automa is the control desk", result.stdout)
        self.assertIn("- vehicles", result.stdout)
        self.assertIn("- simulators", result.stdout)
        self.assertNotIn("automa commands", result.stdout)
        self.assertNotIn("vehicles automation run", result.stdout)

    def test_vehicles_help_shows_only_vehicle_level_commands(self) -> None:
        result = run_automa("vehicles")

        self.assertIn("- active", result.stdout)
        self.assertIn("- status", result.stdout)
        self.assertIn("- update", result.stdout)
        self.assertIn("- automation", result.stdout)
        self.assertIn("- perception", result.stdout)
        self.assertNotIn("./cli/automa vehicles automation run", result.stdout)
        self.assertNotIn("./cli/automa vehicles update perception", result.stdout)

    def test_nested_help_shows_only_that_level(self) -> None:
        result = run_automa("vehicles", "automation", "help")

        self.assertIn("automa vehicles automation commands", result.stdout)
        self.assertIn("- run", result.stdout)
        self.assertIn("- status", result.stdout)
        self.assertNotIn("--interval-s", result.stdout)

    def test_group_without_child_matches_explicit_help(self) -> None:
        for command in (
            ("vehicles",),
            ("vehicles", "update"),
            ("vehicles", "automation"),
            ("simulators",),
        ):
            with self.subTest(command=command):
                implicit = run_automa(*command)
                explicit = run_automa(*command, "help")
                self.assertEqual(implicit.stdout, explicit.stdout)

    def test_perception_help_shows_only_perception_level_commands(self) -> None:
        result = run_automa("vehicles", "perception", "help")

        self.assertIn("automa vehicles perception commands", result.stdout)
        self.assertIn("- run", result.stdout)
        self.assertIn("- apply", result.stdout)
        self.assertNotIn("- replay", result.stdout)
        self.assertIn("- enable", result.stdout)
        self.assertIn("- disable", result.stdout)
        self.assertNotIn("--id", result.stdout)

    def test_operation_help_shows_only_bounded_operations(self) -> None:
        result = run_automa("vehicles", "operation", "help")

        self.assertIn("automa vehicles operation commands", result.stdout)
        self.assertIn("- startup-check", result.stdout)
        self.assertNotIn("--throttle", result.stdout)

    def test_update_help_distinguishes_local_staging_from_physical_deploy(self) -> None:
        result = run_automa("vehicles", "update", "help")

        self.assertIn("- core        deploy physical DonkeyCar harness code", result.stdout)
        self.assertIn("- autonomy    deploy physical autonomy controller release", result.stdout)
        self.assertIn("- perception  stage local vehicle perception code", result.stdout)
        self.assertIn("- decision    stage local decision configuration", result.stdout)

    def test_vehicle_help_labels_local_worker_and_bounded_motion(self) -> None:
        vehicles = run_automa("vehicles", "help")
        automation = run_automa("vehicles", "automation", "help")
        operation = run_automa("vehicles", "operation", "help")

        self.assertIn("manage locally deployed automation workers", vehicles.stdout)
        self.assertIn("show locally deployed worker and view state", automation.stdout)
        self.assertIn("send bounded pulses and verify camera changes", operation.stdout)

    def test_info_and_perception_help_identify_local_staged_state(self) -> None:
        info = run_automa("vehicles", "info", "help")
        perception = run_automa("vehicles", "perception", "help")

        self.assertIn("show staged perception schema and live view", info.stdout)
        self.assertIn("show locally staged decision engine schema", info.stdout)
        self.assertIn("enable one locally staged perception plugin", perception.stdout)
        self.assertIn("disable one locally staged perception plugin", perception.stdout)

    def test_final_command_help_describes_actual_perception_and_decision_scope(self) -> None:
        perception = run_automa("vehicles", "update", "perception", "--help")
        decision = run_automa("vehicles", "update", "decision", "--help")

        self.assertIn("Idempotently stage a perception algorithm", perception.stdout)
        self.assertIn("--candidate", perception.stdout)
        self.assertIn("local simulator only", perception.stdout)
        self.assertIn("Stage a decision engine in the local controller bundle", decision.stdout)

    def test_primary_journey_leaf_help_owns_every_required_flag(self) -> None:
        active = run_automa("vehicles", "active", "--help")
        status = run_automa("vehicles", "status", "--help")
        update = run_automa("vehicles", "update", "perception", "--help")
        run = run_automa("vehicles", "automation", "run", "--help")
        stop = run_automa("vehicles", "automation", "stop", "--help")
        ensure = run_automa("simulators", "ensure", "--help")

        self.assertIn("Discoverable does not mean", active.stdout)
        self.assertIn("--chase-url", status.stdout)
        self.assertIn("CHASE_UI_WS_URL", status.stdout)
        self.assertIn("without starting a simulator", status.stdout)
        self.assertIn("--algorithm", update.stdout)
        self.assertIn("--id", update.stdout)
        for flag in ("--id", "--observe-only", "--frames", "--open-view"):
            self.assertIn(flag, run.stdout)
        self.assertIn("without changing scenario, playback", run.stdout)
        self.assertIn("control source, input, or applying vehicle control", run.stdout)
        self.assertIn("--id", stop.stdout)
        self.assertIn("--scenario", ensure.stdout)
        self.assertIn("This command changes", ensure.stdout)
        self.assertIn("simulator configuration", ensure.stdout)

    def test_durable_primary_sequence_is_parser_valid_and_matches_readme(self) -> None:
        guide = (
            ROOT / "docs" / "reference" / "cli-simulator-perception-journey.md"
        ).read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        commands = (
            "./cli/automa vehicles status --chase-url http://localhost:5050",
            "./cli/automa vehicles update perception --id chase-sim-chaser "
            "--algorithm lightweight_observer",
            "./cli/automa vehicles automation run --id chase-sim-chaser "
            "--observe-only --frames 0 --open-view",
            "./cli/automa vehicles status --id chase-sim-chaser",
            "./cli/automa vehicles automation stop --id chase-sim-chaser",
        )
        parser = build_parser()
        normalized_guide = " ".join(guide.replace("\\\n", " ").split())
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, normalized_guide)
                parser.parse_args(shlex.split(command.removeprefix("./cli/automa ")))
        for phrase in (
            "vehicles status",
            "lightweight_observer",
            "--observe-only",
            "--open-view",
            "automation stop",
        ):
            self.assertIn(phrase, guide)
            self.assertIn(phrase, readme)

    def test_simulators_help_shows_only_simulator_level_commands(self) -> None:
        result = run_automa("simulators")

        self.assertIn("automa simulators commands", result.stdout)
        self.assertIn("- status", result.stdout)
        self.assertIn("- ensure", result.stdout)
        self.assertNotIn("--timeout-ms", result.stdout)

    def test_vehicles_active_rejects_removed_compatibility_aliases(self) -> None:
        for removed_flag in ("--verbose", "--include-inactive"):
            with self.subTest(flag=removed_flag):
                result = run_automa(
                    "vehicles",
                    "active",
                    "--no-picar",
                    "--no-sim",
                    removed_flag,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unrecognized arguments", result.stderr)

        result = run_automa(
            "vehicles",
            "automation",
            "run",
            "--id",
            "chase-sim-chaser",
            "--prepare-control",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
