from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from cli.automa_cli.app import main
from cli.automa_cli.automation import CommandResult, _run_configuration


class RunConfigurationTests(unittest.TestCase):
    def test_unbounded_overrides_counts_in_both_modes(self):
        for take_control in (True, False):
            for count in (0, 3, -1):
                with self.subTest(take_control=take_control, count=count):
                    configuration = _run_configuration(
                        take_control=take_control, interval_s=.25,
                        num_decisions=count, unbounded=True,
                    )
                    self.assertEqual(configuration.num_decisions, 0)
                    self.assertEqual(configuration.mode, "autonomy" if take_control else "observe_only")

    def test_bounded_defaults_and_observation_zero_remain_unchanged(self):
        for take_control in (True, False):
            configuration = _run_configuration(take_control=take_control, interval_s=.25, num_decisions=3)
            self.assertEqual(configuration.num_decisions, 3)
        with self.assertRaisesRegex(ValueError, "use --unbounded"):
            _run_configuration(take_control=True, interval_s=.25, num_decisions=0)
        self.assertEqual(_run_configuration(take_control=False, interval_s=.25, num_decisions=0).num_decisions, 0)
        with self.assertRaises(ValueError):
            _run_configuration(take_control=True, interval_s=.25, num_decisions=-1)
        with self.assertRaises(ValueError):
            _run_configuration(take_control=True, interval_s=-1, num_decisions=3, unbounded=True)

    def test_cli_dispatches_opt_in_to_foreground_background_and_restart(self):
        for verb, extra, target in (
            ("run", [], "start_vehicle_automation_background"),
            ("run", ["--foreground"], "run_vehicle_automation"),
            ("restart", [], "restart_vehicle_automation"),
        ):
            for flags in ([], ["--unbounded"]):
                with (
                    self.subTest(verb=verb, extra=extra, flags=flags),
                    patch(f"cli.automa_cli.app.{target}", return_value=CommandResult(0, "ok")) as run,
                    patch("cli.automa_cli.app.record_vehicle_automation_terminal_result"),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    code = main(["vehicles", "automation", verb, "--id", "picar-test",
                                 "--num-decisions", "3", *flags, *extra])
                    self.assertEqual(code, 0)
                    self.assertEqual(run.call_args.kwargs["unbounded"], bool(flags))
                    self.assertEqual(run.call_args.kwargs["num_decisions"], 3)
