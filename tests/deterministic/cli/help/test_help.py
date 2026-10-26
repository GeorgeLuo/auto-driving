from __future__ import annotations

import contextlib
import io
import unittest

from cli.automa_cli.app import build_parser


class HelpCommandTests(unittest.TestCase):

    def test_operator_journey_parses(self) -> None:
        parser = build_parser()
        for args in (
            ["vehicles", "status"],
            ["vehicles", "discover", "--provider", "chase-sim", "--url", "http://localhost:5051"],
            ["vehicles", "remove", "--url", "http://localhost:5051"],
            ["vehicles", "viewer", "--host-url", "http://127.0.0.1:54665"],
            [
                "vehicles",
                "update",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--preset",
                "lightweight_observer",
            ],
            [
                "vehicles",
                "automation",
                "run",
                "--id",
                "chase-sim-chaser",
                "--observe-only",
                "--num-decisions",
                "0",
                "--open-view",
            ],
            [
                "vehicles", "automation", "restart", "--id", "piracer",
                "--num-decisions", "7",
            ],
            ["vehicles", "status", "--id", "chase-sim-chaser"],
            ["vehicles", "automation", "stop", "--id", "chase-sim-chaser"],
        ):
            with self.subTest(args=args):
                parsed = parser.parse_args(args)
                self.assertEqual(parsed.command, "vehicles")
                if "--num-decisions" in args:
                    self.assertEqual(parsed.num_decisions, int(args[args.index("--num-decisions") + 1]))

    def test_removed_flags_are_rejected_before_execution(self) -> None:
        parser = build_parser()
        for args in (
            ["vehicles", "active", "--verbose"],
            ["vehicles", "active", "--include-inactive"],
            ["vehicles", "automation", "run", "--id", "chase-sim-chaser", "--frames", "3"],
            ["vehicles", "automation", "restart", "--id", "piracer", "--frames", "3"],
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
