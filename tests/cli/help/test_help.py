from __future__ import annotations

import contextlib
import io
import unittest

from cli.automa_cli.app import build_parser
from tests.support.cli_runner import run_automa


class HelpCommandTests(unittest.TestCase):
    def test_public_help_exits_successfully(self) -> None:
        result = run_automa("help")
        self.assertTrue(result.stdout.strip())
        self.assertEqual(result.stderr, "")

    def test_operator_journey_parses(self) -> None:
        parser = build_parser()
        for args in (
            ["vehicles", "status", "--chase-url", "http://localhost:5050"],
            [
                "vehicles",
                "update",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--algorithm",
                "lightweight_observer",
            ],
            [
                "vehicles",
                "automation",
                "run",
                "--id",
                "chase-sim-chaser",
                "--observe-only",
                "--frames",
                "0",
                "--open-view",
            ],
            ["vehicles", "status", "--id", "chase-sim-chaser"],
            ["vehicles", "automation", "stop", "--id", "chase-sim-chaser"],
        ):
            with self.subTest(args=args):
                parsed = parser.parse_args(args)
                self.assertEqual(parsed.command, "vehicles")

    def test_removed_flags_are_rejected_before_execution(self) -> None:
        parser = build_parser()
        for args in (
            ["vehicles", "active", "--verbose"],
            ["vehicles", "active", "--include-inactive"],
            [
                "vehicles",
                "automation",
                "run",
                "--id",
                "chase-sim-chaser",
                "--prepare-control",
            ],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
