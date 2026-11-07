from __future__ import annotations

import contextlib
import io
import json
import unittest

from cli.automa_cli.app import build_parser


class HelpCommandTests(unittest.TestCase):

    def test_workbench_is_discoverable_at_the_top_level(self) -> None:
        from tests.shared.cli.invoke import invoke_automa

        for args in (("help",), ("--help",)):
            with self.subTest(args=args):
                self.assertIn("workbench", invoke_automa(*args).stdout)
        for args in (("hosts", "help"), ("hosts", "--help")):
            with self.subTest(args=args):
                self.assertNotIn("workbench", invoke_automa(*args).stdout)
        details = invoke_automa("workbench", "--help").stdout
        self.assertIn("usage: automa workbench", details)
        self.assertIn("source_dir", details)
        self.assertIn("--serve", details)
        self.assertIn("--perception-preset", details)

    def test_collection_upload_and_arm_inputs(self) -> None:
        from cli.automa_cli.app import main

        parser = build_parser()
        args = parser.parse_args(["plugins", "upload", "--url", "http://localhost:1", "--manifest", "collection.json"])
        self.assertEqual(str(args.manifest), "collection.json")
        args = parser.parse_args(["plugins", "arm", "--host-name", "one", "--collection", "experiment"])
        self.assertEqual(args.collection, "experiment")
        args = parser.parse_args(["plugins", "disarm", "--host-name", "one", "--collection", "experiment"])
        self.assertEqual((args.collection, args.plugins, args.step, args.all_plugins), ("experiment", None, None, False))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["plugins", "disarm", "--host-name", "one", "--collection", "experiment", "--all"])
        for options, reason in (
            (["--file", "p.py", "--name", "perception/x"], "--file requires --name and --entrypoint"),
            (["--manifest", "m.json", "--entrypoint", "x:X"], "use --name, --entrypoint and --config with --file"),
        ):
            with self.subTest(options=options), contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["plugins", "upload", "--host-name", "one", *options, "--json"]), 2)
                receipt = json.loads(printed.getvalue())
                self.assertEqual((receipt["ok"], receipt["outcome"]["recovery"]), (False, None))
                self.assertIn(reason, receipt["error"])

    def test_plugins_help_lists_inspect_and_inspect_help_lists_all_five_steps(self) -> None:
        from tests.shared.cli.invoke import invoke_automa

        top = invoke_automa("plugins").stdout
        self.assertIn("- inspect", top)
        self.assertNotIn("- perception", top)
        steps = invoke_automa("plugins", "inspect").stdout
        for step in ("perception", "memory", "proposal", "plan", "action"):
            self.assertIn(f"- {step}", steps)
        self.assertNotIn("Act on proposals", steps)


if __name__ == "__main__":
    unittest.main()
