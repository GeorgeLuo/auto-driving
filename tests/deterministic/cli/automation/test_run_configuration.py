from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.shared.command_result import CommandResult
from cli.automa_cli.app import main
from cli.automa_cli.hosts.runs.automation_stop import WORKER_MODULE, _automation_command_matches_host
from cli.automa_cli.hosts.runs.automation import (
    _run_configuration,
    _run_worker,
    run_host_automation,
    start_host_automation_background,
)


class _Host:
    def __init__(self, status: dict) -> None:
        self.status = status

    def host_status(self) -> dict:
        return self.status


class _HostClient:
    def __init__(self, _url: str, timeout_s: float = 0.1) -> None:
        del timeout_s
        self.host = _Host({"ok": True, "host_name": "chase-sim-chaser"})

    def host_status(self) -> dict:
        return self.host.host_status()


class RunConfigurationTests(unittest.TestCase):
    def test_process_recognition_requires_the_worker_module_and_exact_host_name(self):
        worker = f"python -m {WORKER_MODULE}"
        self.assertTrue(_automation_command_matches_host(f"{worker} --name car --timeout-s 5", "car"))
        self.assertTrue(_automation_command_matches_host(f"/venv/bin/python3 -m {WORKER_MODULE} --name=car", "car"))
        legacy = "python -m cli.automa_cli.hosts.automation"
        self.assertTrue(_automation_command_matches_host(f"{legacy} --name car", "car"))
        self.assertTrue(_automation_command_matches_host(f"{legacy} --name=car", "car"))
        self.assertFalse(_automation_command_matches_host(f"{legacy} --name car-2", "car"))
        self.assertFalse(_automation_command_matches_host(f"{worker} --name car-2", "car"))
        self.assertFalse(_automation_command_matches_host(f"{worker} --host-url http://car", "car"))
        self.assertFalse(_automation_command_matches_host("python -m cli.automa_cli.hosts.runtime_hosts car", "car"))
        self.assertFalse(_automation_command_matches_host(f"python {WORKER_MODULE} --name car", "car"))
        self.assertFalse(_automation_command_matches_host("python /tmp/cli/automa hosts run --name car", "car"))

    def test_unbounded_overrides_counts_in_both_modes(self):
        for take_control in (True, False):
            for count in (None, 3):
                with self.subTest(take_control=take_control, count=count):
                    configuration = _run_configuration(
                        take_control=take_control,
                        num_decisions=count, unbounded=True,
                    )
                    self.assertIsNone(configuration.num_decisions)
                    self.assertEqual(configuration.mode, "autonomy" if take_control else "observe_only")
            # The runtime refuses a count below one, so --unbounded doesn't accept one.
            for count in (0, -1):
                with (
                    self.subTest(take_control=take_control, count=count),
                    self.assertRaisesRegex(ValueError, "positive integer"),
                ):
                    _run_configuration(take_control=take_control,
                                       num_decisions=count, unbounded=True)

    def test_movement_needs_a_count_and_observation_without_one_is_unbounded(self):
        for take_control in (True, False):
            configuration = _run_configuration(take_control=take_control, num_decisions=3)
            self.assertEqual(configuration.num_decisions, 3)
            with self.assertRaisesRegex(ValueError, "positive integer"):
                _run_configuration(take_control=take_control, num_decisions=0)
        with self.assertRaisesRegex(ValueError, "use --unbounded"):
            _run_configuration(take_control=True, num_decisions=None)
        self.assertIsNone(_run_configuration(take_control=False, num_decisions=None).num_decisions)
        with self.assertRaises(ValueError):
            _run_configuration(take_control=True, num_decisions=-1)
        with self.assertRaises(TypeError):
            RunConfiguration(interval_s=0.25, num_decisions=3)

    def test_cli_dispatches_run_and_restart(self):
        for verb, target in (
            ("run", "start_host_automation_background"),
            ("restart", "restart_host_automation"),
        ):
            for count, flags in ((["--num-decisions", "3"], []), (["--num-decisions", "3"], ["--unbounded"]), ([], [])):
                with (
                    self.subTest(verb=verb, count=count, flags=flags),
                    patch(f"cli.automa_cli.hosts.commands.{target}", return_value=CommandResult(0, "ok")) as run,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    code = main(["hosts", verb, "--name", "picar-test", *count, *flags])
                    self.assertEqual(code, 0)
                    self.assertEqual(run.call_args.kwargs["unbounded"], bool(flags))
                    self.assertEqual(run.call_args.kwargs["num_decisions"], 3 if count else None)
                    if target != "restart_host_automation":
                        self.assertIsNone(run.call_args.kwargs["host_url"])

    def test_retired_foreground_flag_is_refused(self):
        with (
            self.assertRaises(SystemExit) as raised,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            main(["hosts", "run", "--name", "picar-test", "--num-decisions", "1", "--foreground"])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_dispatches_host_url_without_an_id(self):
        with (
            patch("cli.automa_cli.hosts.commands.start_host_automation_background",
                  return_value=CommandResult(0, "ok")) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = main([
                "hosts", "run",
                "--host-url", "http://127.0.0.1:54355",
                "--num-decisions", "1",
            ])
        self.assertEqual(code, 0)
        self.assertIsNone(run.call_args.kwargs["host_name"])
        self.assertEqual(run.call_args.kwargs["host_url"], "http://127.0.0.1:54355")

    def test_cli_rejects_both_or_neither_target(self):
        for argv in (
            ["hosts", "run", "--num-decisions", "1"],
            [
                "hosts", "run", "--name", "chase-sim-chaser",
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

    def test_host_url_runs_that_host_and_reads_its_host_name(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("cli.automa_cli.hosts.runtime_hosts.RUNTIME_ROOT", Path(tmp)),
            patch("cli.automa_cli.shared.controller_releases.RUNTIME_ROOT", Path(tmp)),
            patch("cli.automa_cli.hosts.runs.automation.runtime_base_url", side_effect=AssertionError("started a host")) as started,
            patch("cli.automa_cli.hosts.runs.automation.RuntimeClient", _HostClient),
            patch("cli.automa_cli.hosts.runs.automation.monitor_runtime", return_value=(0, "ran")) as monitor,
        ):
            result = run_host_automation(
                host_url="http://127.0.0.1:54355/", num_decisions=1, take_control=True,
            )
        self.assertEqual(result.exit_code, 0, result.message)
        started.assert_not_called()
        self.assertEqual(monitor.call_args.kwargs["host_name"], "chase-sim-chaser")
        self.assertEqual(monitor.call_args.kwargs["base_url"], "http://127.0.0.1:54355")

    def test_host_url_without_a_host_does_not_start_one(self):
        for host_url in ("not-a-host", "http://127.0.0.1:54355/host/status"):
            with (
                self.subTest(host_url=host_url),
                patch("cli.automa_cli.hosts.runs.automation.runtime_base_url", side_effect=AssertionError("started a host")) as started,
            ):
                result = run_host_automation(host_url=host_url, num_decisions=1, take_control=True)
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
                patch("cli.automa_cli.hosts.runs.automation.RuntimeClient", client),
                patch("cli.automa_cli.hosts.runs.automation.runtime_base_url", side_effect=AssertionError("started a host")) as started,
            ):
                result = run_host_automation(
                    host_url="http://127.0.0.1:54355", num_decisions=1, take_control=True,
                )
            self.assertEqual(result.exit_code, 2, result.message)
            started.assert_not_called()

    def _background_command(self, **kwargs) -> list[str]:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("cli.automa_cli.hosts.runtime_hosts.RUNTIME_ROOT", Path(tmp)),
            patch("cli.automa_cli.shared.controller_releases.RUNTIME_ROOT", Path(tmp)),
            patch("cli.automa_cli.hosts.runs.automation.RuntimeClient", _HostClient),
            patch("cli.automa_cli.hosts.runs.automation.subprocess.Popen") as popen,
            patch(
                "cli.automa_cli.hosts.runs.automation._wait_for_automation_startup",
                return_value={"status": "completed"},
            ),
        ):
            popen.return_value.pid = 99
            result = start_host_automation_background(host_url="http://127.0.0.1:54355", **kwargs)
        self.assertEqual(result.exit_code, 0, result.message)
        return popen.call_args.args[0]

    def test_background_command_names_the_host_url(self):
        command = self._background_command(num_decisions=1, take_control=True)
        self.assertEqual(command[1:3], ["-m", WORKER_MODULE])
        self.assertIn("--host-url", command)
        self.assertIn("http://127.0.0.1:54355", command)
        self.assertNotIn("--name", command)

    def test_worker_runs_the_configuration_the_background_command_names(self):
        for kwargs, expected in (
            ({"num_decisions": 2, "take_control": True}, {"num_decisions": 2, "unbounded": False}),
            ({"take_control": False}, {"num_decisions": None, "unbounded": True}),
            ({"num_decisions": 4, "unbounded": True, "take_control": True},
             {"num_decisions": None, "unbounded": True}),
        ):
            with self.subTest(kwargs=kwargs):
                command = self._background_command(record=True, **kwargs)
                with (
                    patch("cli.automa_cli.hosts.runs.automation.run_host_automation",
                          return_value=CommandResult(0, "ran")) as run,
                    patch("cli.automa_cli.hosts.runs.automation.record_host_automation_terminal_result") as record,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(_run_worker(command[3:]), 0)
                self.assertEqual(run.call_args.kwargs["host_url"], "http://127.0.0.1:54355")
                self.assertIsNone(run.call_args.kwargs["host_name"])
                self.assertEqual(run.call_args.kwargs["num_decisions"], expected["num_decisions"])
                self.assertEqual(run.call_args.kwargs["unbounded"], expected["unbounded"])
                self.assertEqual(run.call_args.kwargs["take_control"], kwargs["take_control"])
                self.assertTrue(run.call_args.kwargs["record"])
                # Only a named host has the startup state that records a failure.
                record.assert_not_called()

    def test_worker_records_a_named_host_failure(self):
        with (
            patch("cli.automa_cli.hosts.runs.automation.run_host_automation",
                  return_value=CommandResult(2, "no host")) as run,
            patch("cli.automa_cli.hosts.runs.automation.record_host_automation_terminal_result") as record,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            code = _run_worker(["--name", "car", "--timeout-s", "5", "--observe-only"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "no host\n")
        self.assertEqual(run.call_args.kwargs["host_name"], "car")
        self.assertEqual(run.call_args.kwargs["timeout_s"], 5.0)
        record.assert_called_once_with(host_name="car", result=run.return_value)
