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
            ["hosts", "viewer", "--name", "chase-sim-chaser"],
            ["plugins", "arm", "--host-name", "chase-sim-chaser", "--name", "perception/frame"],
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
            ["hosts", "stage", "perception", "--name", "demo"],
            ["hosts", "perception", "viability", "--name", "demo"],
            ["hosts", "memory", "viability", "--name", "demo"],
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

    def test_plugin_listing_parses_with_or_without_a_host(self) -> None:
        parser = build_parser()
        listed = parser.parse_args(["plugins", "list", "--host-name", "one", "--step", "memory", "--json"])
        self.assertEqual((listed.command, listed.plugin_command, listed.host_name, listed.step),
                         ("plugins", "list", "one", "memory"))
        self.assertTrue(listed.json)
        self.assertFalse(listed.long)
        packaged = parser.parse_args(["plugins", "list"])
        self.assertEqual((packaged.host_name, packaged.url), (None, None))
        by_url = parser.parse_args(["plugins", "list", "--url", "http://127.0.0.1:5051", "--long"])
        self.assertEqual((by_url.url, by_url.long), ("http://127.0.0.1:5051", True))
        for args in (
            ["hosts", "plugins", "list", "--name", "one"],
            ["hosts", "plugins", "status", "--name", "one"],
            ["plugins", "list", "--host-name", "one", "--url", "http://127.0.0.1:5051"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)

    def test_plugin_upload_parses_a_step_plugin_name(self) -> None:
        parser = build_parser()
        file = ["--file", "p.py", "--entrypoint", "prototype:Prototype"]
        uploaded = parser.parse_args(["plugins", "upload", "--host-name", "one", *file,
                                      "--name", "perception/prototype", "--arm"])
        self.assertEqual((uploaded.command, uploaded.plugin_command, uploaded.host_name), ("plugins", "upload", "one"))
        self.assertEqual((uploaded.name, uploaded.arm, uploaded.json), (("perception", "prototype"), True, False))
        for args in (
            ["hosts", "plugins"],
            ["hosts", "plugins", "upload", "--name", "one", *file, "--step", "perception", "--plugin-id", "prototype"],
            ["plugins", "upload", "--host-name", "one", *file, "--step", "perception", "--plugin-id", "prototype"],
            ["plugins", "upload", "--host-name", "one", *file, "--name", "prototype"],
            ["plugins", "upload", "--host-name", "one", "--entrypoint", "prototype:Prototype", "--name", "perception/x"],
            ["plugins", "upload", "--host-name", "one", "--file", "p.py", "--name", "perception/x"],
            ["plugins", "upload", *file, "--name", "perception/x"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)

    def test_plugin_arm_parses_step_plugin_names(self) -> None:
        parser = build_parser()
        armed = parser.parse_args(["plugins", "arm", "--host-name", "one",
                                   "--name", "perception/classical_regions", "--name", "memory/x"])
        self.assertEqual((armed.command, armed.plugin_command, armed.host_name), ("plugins", "arm", "one"))
        self.assertEqual(armed.plugins, [("perception", "classical_regions"), ("memory", "x")])
        for args in (
            ["hosts", "plugins", "arm", "--name", "one", "--step", "perception", "--plugin", "x"],
            ["plugins", "arm", "--host-name", "one"],
            ["plugins", "arm", "--name", "perception/x"],
            ["plugins", "arm", "--host-name", "one", "--name", "classical_regions"],
            ["plugins", "arm", "--host-name", "one", "--name", "sensing/x"],
            ["plugins", "arm", "--host-name", "one", "--name", "perception/"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)

    def test_plugin_disarm_parses_against_a_host(self) -> None:
        parser = build_parser()
        disarmed = parser.parse_args(["plugins", "disarm", "--host-name", "one", "--all"])
        self.assertEqual((disarmed.command, disarmed.plugin_command, disarmed.host_name, disarmed.all_plugins),
                         ("plugins", "disarm", "one", True))
        self.assertFalse(disarmed.json)
        named = parser.parse_args(["plugins", "disarm", "--host-name", "one",
                                   "--name", "proposal/x", "--name", "memory/y"])
        self.assertEqual((named.plugins, named.step), ([("proposal", "x"), ("memory", "y")], None))
        cleared = parser.parse_args(["plugins", "disarm", "--host-name", "one", "--step", "memory"])
        self.assertEqual((cleared.plugins, cleared.step, cleared.all_plugins), (None, "memory", False))
        for args in (
            ["hosts", "plugins", "disarm", "--name", "one", "--all"],
            ["plugins", "disarm", "--all"],
            ["plugins", "disarm", "--host-name", "one"],
            ["plugins", "disarm", "--host-name", "one", "--url", "http://127.0.0.1:5051", "--all"],
            ["plugins", "disarm", "--host-name", "one", "--all", "--name", "proposal/x"],
            ["plugins", "disarm", "--host-name", "one", "--step", "memory", "--all"],
            ["plugins", "disarm", "--host-name", "one", "--step", "plan"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)

    def test_plugin_stream_parses_a_step_against_a_host(self) -> None:
        parser = build_parser()
        streamed = parser.parse_args(["plugins", "stream", "--host-name", "one", "--step", "plan", "--once"])
        self.assertEqual((streamed.command, streamed.plugin_command, streamed.host_name, streamed.step),
                         ("plugins", "stream", "one", "plan"))
        self.assertEqual((streamed.once, streamed.no_clear, streamed.json, streamed.refresh_s), (True, False, False, 0.5))
        for args in (
            ["hosts", "stream", "plan", "--name", "one"],
            ["plugins", "stream", "--host-name", "one"],
            ["plugins", "stream", "--step", "plan"],
            ["plugins", "stream", "--host-name", "one", "--step", "observation"],
            ["plugins", "stream", "--name", "one", "--step", "plan"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)

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

    def test_plugins_help_lists_inspect_and_inspect_help_lists_all_five_steps(self) -> None:
        from tests.shared.cli.invoke import invoke_automa

        top = invoke_automa("plugins").stdout
        self.assertIn("- inspect", top)
        self.assertNotIn("- perception", top)
        steps = invoke_automa("plugins", "inspect").stdout
        for step in ("perception", "memory", "proposal", "plan", "action"):
            self.assertIn(f"- {step}", steps)
        self.assertNotIn("Act on proposals", steps)

    def test_step_inspect_parses_under_plugins(self) -> None:
        parser = build_parser()
        for step in ("perception", "memory", "proposal", "plan", "action"):
            with self.subTest(step=step):
                parsed = parser.parse_args(["plugins", "inspect", step, "frame.png"])
                self.assertEqual(parsed.command, "plugins")
                self.assertEqual(parsed.plugin_command, "inspect")
                self.assertEqual(parsed.step, step)
        for args in (
            ["plugins", "inspect", "observation", "frame.png"],
            ["hosts", "proposal", "inspect", "frame.png"],
            ["hosts", "plan", "inspect", "frame.png"],
            ["hosts", "action", "inspect", "frame.png"],
            ["hosts", "perception", "inspect", "frame.png"],
            ["hosts", "memory", "inspect", "frame.png"],
            ["plugins", "inspect", "perception", "--name", "piracer"],
            ["plugins", "inspect", "perception", "frame.png", "--frames", "1"],
            ["plugins", "inspect", "perception", "frame.png", "--interval-s", "0"],
            ["plugins", "inspect", "perception", "frame.png", "--timeout-s", "3"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)
        for step in ("perception", "memory", "proposal", "plan", "action"):
            with self.subTest(step=step, missing_source=True), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(["plugins", "inspect", step])
                self.assertEqual(caught.exception.code, 2)

    def test_the_viewer_takes_one_host_by_name_or_url(self) -> None:
        parser = build_parser()
        named = parser.parse_args(["hosts", "viewer", "--name", "chase-sim-chaser"])
        self.assertEqual((named.host_name, named.host_url), ("chase-sim-chaser", None))
        for args in (
            ["hosts", "viewer"],
            ["hosts", "viewer", "--name", "chase-sim-chaser", "--host-url", "http://127.0.0.1:54665"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(args)
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
