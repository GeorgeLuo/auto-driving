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
            ["host", "viewer", "--host-url", "http://127.0.0.1:54665"],
            [
                "host", "stage",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--preset",
                "lightweight_observer",
            ],
            [
                "host",
                "run",
                "--id",
                "chase-sim-chaser",
                "--observe-only",
                "--num-decisions",
                "0",
                "--open-view",
            ],
            [
                "host", "restart", "--id", "piracer",
                "--num-decisions", "7",
            ],
            ["vehicles", "status"],
            ["host", "stop", "--id", "chase-sim-chaser"],
        ):
            with self.subTest(args=args):
                parsed = parser.parse_args(args)
                self.assertEqual(parsed.command, args[0])
                if "--num-decisions" in args:
                    self.assertEqual(parsed.num_decisions, int(args[args.index("--num-decisions") + 1]))

    def test_host_lifecycle_parses_under_one_group(self) -> None:
        parser = build_parser()
        for args in (
            ["create", "--id", "demo", "--provider", "chase-sim", "--url", "http://localhost:5050"],
            ["update", "--id", "demo", "--restart"],
            ["run", "--id", "demo"],
            ["stop", "--id", "demo"],
            ["restart", "--id", "demo"],
            ["status", "--id", "demo"],
            ["serve", "--id", "demo"],
            ["delete", "--id", "demo"],
        ):
            with self.subTest(args=args):
                parsed = parser.parse_args(["host", *args])
                self.assertEqual((parsed.host_command, parsed.host_id), (args[0], "demo"))
        for args in (
            ["vehicles", "host", "status"],
            ["vehicles", "plugins", "status", "--id", "demo"],
            ["vehicles", "update", "perception", "--id", "demo"],
            ["vehicles", "automation", "run", "--id", "demo"],
            ["vehicles", "create", "host", "--id", "demo", "--provider", "chase-sim", "--url", "http://localhost:5050"],
            ["host", "stage", "host", "--id", "demo"],
            ["vehicles", "delete", "host", "--id", "demo"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)

    def test_simulator_lifecycle_parses(self) -> None:
        parser = build_parser()

        self.assertEqual(parser.parse_args(["simulators", "create"]).simulator_command, "create")
        deleted = parser.parse_args(["simulators", "delete", "--url", "http://127.0.0.1:5051/"])
        self.assertEqual((deleted.simulator_command, deleted.url), ("delete", "http://127.0.0.1:5051/"))

    def test_removed_flags_are_rejected_before_execution(self) -> None:
        parser = build_parser()
        for args in (
            ["vehicles", "active", "--verbose"],
            ["vehicles", "active", "--include-inactive"],
            ["host", "run", "--id", "chase-sim-chaser", "--frames", "3"],
            ["host", "restart", "--id", "piracer", "--frames", "3"],
            [
                "host",
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
