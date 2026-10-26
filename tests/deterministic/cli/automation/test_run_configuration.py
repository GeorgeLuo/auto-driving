from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.app import main
from cli.automa_cli.automation import (
    CommandResult,
    _run_configuration,
    run_vehicle_automation,
    start_vehicle_automation_background,
)


class _Host:
    def __init__(self, status: dict) -> None:
        self.status = status

    def host_status(self) -> dict:
        return self.status


class _HostClient:
    def __init__(self, _url: str, timeout_s: float = 0.1) -> None:
        del timeout_s
        self.host = _Host({"ok": True, "vehicle_id": "chase-sim-chaser"})

    def host_status(self) -> dict:
        return self.host.host_status()


class RunConfigurationTests(unittest.TestCase):
    def test_unbounded_overrides_counts_in_both_modes(self):
        for take_control in (True, False):
            for count in (0, 3):
                with self.subTest(take_control=take_control, count=count):
                    configuration = _run_configuration(
                        take_control=take_control,
                        num_decisions=count, unbounded=True,
                    )
                    self.assertEqual(configuration.num_decisions, 0)
                    self.assertEqual(configuration.mode, "autonomy" if take_control else "observe_only")
            # The runtime refuses a negative count, so --unbounded doesn't accept one.
            with (
                self.subTest(take_control=take_control, count=-1),
                self.assertRaisesRegex(ValueError, "nonnegative"),
            ):
                _run_configuration(take_control=take_control,
                                   num_decisions=-1, unbounded=True)

    def test_bounded_defaults_and_observation_zero_remain_unchanged(self):
        for take_control in (True, False):
            configuration = _run_configuration(take_control=take_control, num_decisions=3)
            self.assertEqual(configuration.num_decisions, 3)
        with self.assertRaisesRegex(ValueError, "use --unbounded"):
            _run_configuration(take_control=True, num_decisions=0)
        self.assertEqual(_run_configuration(take_control=False, num_decisions=0).num_decisions, 0)
        with self.assertRaises(ValueError):
            _run_configuration(take_control=True, num_decisions=-1)
        with self.assertRaises(TypeError):
            RunConfiguration(interval_s=0.25, num_decisions=3)

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
                    code = main(["vehicles", "host", verb, "--id", "picar-test",
                                 "--num-decisions", "3", *flags, *extra])
                    self.assertEqual(code, 0)
                    self.assertEqual(run.call_args.kwargs["unbounded"], bool(flags))
                    self.assertEqual(run.call_args.kwargs["num_decisions"], 3)
                    if target != "restart_vehicle_automation":
                        self.assertIsNone(run.call_args.kwargs["host_url"])

    def test_cli_dispatches_host_url_without_an_id(self):
        for extra, target in (
            ([], "start_vehicle_automation_background"),
            (["--foreground"], "run_vehicle_automation"),
        ):
            with (
                self.subTest(extra=extra),
                patch(f"cli.automa_cli.app.{target}", return_value=CommandResult(0, "ok")) as run,
                patch("cli.automa_cli.app.record_vehicle_automation_terminal_result"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main([
                    "vehicles", "host", "run",
                    "--host-url", "http://127.0.0.1:54355",
                    "--num-decisions", "1", *extra,
                ])
                self.assertEqual(code, 0)
                self.assertIsNone(run.call_args.kwargs["vehicle_id"])
                self.assertEqual(run.call_args.kwargs["host_url"], "http://127.0.0.1:54355")

    def test_cli_rejects_both_or_neither_target(self):
        for argv in (
            ["vehicles", "host", "run", "--num-decisions", "1"],
            [
                "vehicles", "host", "run", "--id", "chase-sim-chaser",
                "--host-url", "http://127.0.0.1:54355", "--num-decisions", "1",
            ],
        ):
            with (
                self.subTest(argv=argv),
                self.assertRaises(SystemExit) as raised,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                main(argv)
            self.assertEqual(raised.exception.code, 2)

    def test_host_url_runs_that_host_and_reads_its_vehicle_id(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("cli.automa_cli.runtime_hosts.RUNTIME_ROOT", Path(tmp)),
            patch("cli.automa_cli.automation.runtime_base_url", side_effect=AssertionError("started a host")) as started,
            patch("cli.automa_cli.automation.RuntimeClient", _HostClient),
            patch("cli.automa_cli.automation.monitor_runtime", return_value=(0, "ran")) as monitor,
        ):
            result = run_vehicle_automation(
                host_url="http://127.0.0.1:54355/", num_decisions=1, take_control=True,
            )
        self.assertEqual(result.exit_code, 0, result.message)
        started.assert_not_called()
        self.assertEqual(monitor.call_args.kwargs["vehicle_id"], "chase-sim-chaser")
        self.assertEqual(monitor.call_args.kwargs["base_url"], "http://127.0.0.1:54355")

    def test_host_url_without_a_host_does_not_start_one(self):
        for host_url in ("not-a-host", "http://127.0.0.1:54355/host/status"):
            with (
                self.subTest(host_url=host_url),
                patch("cli.automa_cli.automation.runtime_base_url", side_effect=AssertionError("started a host")) as started,
            ):
                result = run_vehicle_automation(host_url=host_url, num_decisions=1, take_control=True)
            self.assertEqual(result.exit_code, 2, result.message)
            started.assert_not_called()

    def test_host_url_refuses_a_host_that_does_not_report_an_id(self):
        class _Silent:
            def __init__(self, _url: str, timeout_s: float = 0.1) -> None:
                del timeout_s

            def host_status(self) -> dict:
                return {"ok": True}

        class _Down:
            def __init__(self, _url: str, timeout_s: float = 0.1) -> None:
                del timeout_s

            def host_status(self) -> dict:
                raise RuntimeError("connection refused")

        for client in (_Silent, _Down):
            with (
                self.subTest(client=client.__name__),
                patch("cli.automa_cli.automation.RuntimeClient", client),
                patch("cli.automa_cli.automation.runtime_base_url", side_effect=AssertionError("started a host")) as started,
            ):
                result = run_vehicle_automation(
                    host_url="http://127.0.0.1:54355", num_decisions=1, take_control=True,
                )
            self.assertEqual(result.exit_code, 2, result.message)
            started.assert_not_called()

    def test_background_command_names_the_host_url(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("cli.automa_cli.runtime_hosts.RUNTIME_ROOT", Path(tmp)),
            patch("cli.automa_cli.automation.RuntimeClient", _HostClient),
            patch("cli.automa_cli.automation.subprocess.Popen") as popen,
            patch(
                "cli.automa_cli.automation._wait_for_automation_startup",
                return_value={"status": "completed"},
            ),
        ):
            popen.return_value.pid = 99
            result = start_vehicle_automation_background(
                host_url="http://127.0.0.1:54355", num_decisions=1, take_control=True,
            )
        self.assertEqual(result.exit_code, 0, result.message)
        command = popen.call_args.args[0]
        self.assertIn("--host-url", command)
        self.assertIn("http://127.0.0.1:54355", command)
        self.assertNotIn("--id", command)
