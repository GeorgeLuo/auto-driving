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
            ["hosts", "viewer", "--host-url", "http://127.0.0.1:54665"],
            [
                "hosts", "stage",
                "perception",
                "--name",
                "chase-sim-chaser",
                "--preset",
                "lightweight_observer",
            ],
            [
                "hosts",
                "run",
                "--name",
                "chase-sim-chaser",
                "--observe-only",
                "--num-decisions",
                "0",
                "--open-view",
            ],
            [
                "hosts", "restart", "--name", "piracer",
                "--num-decisions", "7",
            ],
            ["vehicles", "status"],
            ["hosts", "stop", "--name", "chase-sim-chaser"],
        ):
            with self.subTest(args=args):
                parsed = parser.parse_args(args)
                self.assertEqual(parsed.command, args[0])
                if "--num-decisions" in args:
                    self.assertEqual(parsed.num_decisions, int(args[args.index("--num-decisions") + 1]))

    def test_host_lifecycle_parses_under_one_group(self) -> None:
        parser = build_parser()
        for args in (
            ["create", "--name", "demo", "--provider", "chase-sim", "--url", "http://localhost:5050"],
            ["update", "--name", "demo", "--restart"],
            ["run", "--name", "demo"],
            ["stop", "--name", "demo"],
            ["restart", "--name", "demo"],
            ["status", "--name", "demo"],
            ["serve", "--name", "demo"],
            ["delete", "--name", "demo"],
        ):
            with self.subTest(args=args):
                parsed = parser.parse_args(["hosts", *args])
                self.assertEqual((parsed.host_command, parsed.host_name), (args[0], "demo"))
        for args in (
            ["host", "status"],
            ["host", "run", "--name", "demo"],
            ["vehicles", "host", "status"],
            ["vehicles", "plugins", "status", "--name", "demo"],
            ["vehicles", "update", "perception", "--name", "demo"],
            ["vehicles", "automation", "run", "--name", "demo"],
            ["vehicles", "create", "host", "--name", "demo", "--provider", "chase-sim", "--url", "http://localhost:5050"],
            ["hosts", "stage", "host", "--name", "demo"],
            ["vehicles", "delete", "host", "--name", "demo"],
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
            ["hosts", "run", "--name", "chase-sim-chaser", "--frames", "3"],
            ["hosts", "restart", "--name", "piracer", "--frames", "3"],
            [
                "hosts",
                "run",
                "--name",
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
