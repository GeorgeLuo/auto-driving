from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from cli.automa_cli.hosts.runs.models import StartResult
from unittest.mock import patch

from tests.shared.cli.invoke import invoke_automa


INVALID_TIMEOUTS = ("0", "-1", "nan", "-nan", "inf", "+inf", "-inf")
TIMEOUT_MESSAGE = "--timeout-s must be a finite number greater than zero"
# Each status command and the function its timeout reaches.
STATUS_COMMANDS = {
    ("vehicles", "status"): "cli.automa_cli.vehicles.commands.get_vehicles_status",
    ("hosts", "status"): "cli.automa_cli.hosts.runs.commands.get_hosts_status",
}


def _invoke(*args: str) -> tuple[int, str, str]:
    result = invoke_automa(*args, check=False)
    return result.returncode, result.stdout, result.stderr


def _timeout_args(value: str) -> tuple[str, ...]:
    if value in {"-nan", "-inf"}:
        return (f"--timeout-s={value}",)
    return ("--timeout-s", value)


class TimeoutInputTests(unittest.TestCase):
    def test_invalid_status_values_use_one_json_error_envelope_before_status_work(self) -> None:
        for (command, target), value in ((item, value) for item in STATUS_COMMANDS.items() for value in INVALID_TIMEOUTS):
            with self.subTest(command=command, value=value), patch(target) as get_status:
                code, stdout, stderr = _invoke(*command, *_timeout_args(value), "--json")

            self.assertEqual(code, 2)
            self.assertEqual(stderr, "")
            self.assertNotIn("Traceback", stdout)
            payload = json.loads(stdout)
            self.assertEqual(payload["schema"], "automa_cli_error")
            self.assertEqual(payload["error_code"], "timeout_invalid")
            self.assertEqual(payload["layer"], "input")
            self.assertEqual(payload["exit_code"], 2)
            self.assertEqual(payload["details"]["argument"], "--timeout-s")
            self.assertIn(TIMEOUT_MESSAGE, payload["error"])
            self.assertNotIn("message", payload)
            get_status.assert_not_called()

    def test_invalid_status_values_use_the_existing_human_error_channel(self) -> None:
        for (command, target), value in ((item, value) for item in STATUS_COMMANDS.items() for value in INVALID_TIMEOUTS):
            with self.subTest(command=command, value=value), patch(target) as get_status:
                code, stdout, stderr = _invoke(*command, *_timeout_args(value))

            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, f"automa {' '.join(command)}: {TIMEOUT_MESSAGE}\n")
            get_status.assert_not_called()

    def test_invalid_automation_values_stop_before_worker_dispatch(self) -> None:
        for value in INVALID_TIMEOUTS:
            with self.subTest(value=value):
                with patch("cli.automa_cli.hosts.runs.commands.start_host_automation_background") as start_automation:
                    code, stdout, stderr = _invoke(
                        "hosts",
                        "run",
                        "--name",
                        "chase-sim-chaser",
                        *_timeout_args(value),
                    )

                self.assertEqual(code, 2)
                self.assertIn("automa hosts run", stdout)
                self.assertIn(TIMEOUT_MESSAGE, stdout)
                self.assertNotIn("Traceback", stdout)
                self.assertEqual(stderr, "")
                start_automation.assert_not_called()

    def test_valid_positive_timeout_reaches_each_consumer_unchanged(self) -> None:
        with patch(
            "cli.automa_cli.vehicles.commands.get_vehicles_status",
            return_value={"vehicles": [{"provider": "chase-sim", "url": "http://localhost:5050/", "state": "running"}]},
        ) as get_status:
            code, _, _ = _invoke("vehicles", "status", "--timeout-s", "1.25")
        self.assertEqual(code, 0)
        get_status.assert_called_once_with(timeout_s=1.25)

        with patch(
            "cli.automa_cli.hosts.runs.commands.get_hosts_status", return_value=SimpleNamespace(exit_code=0, message="ok"),
        ) as get_hosts:
            code, _, _ = _invoke("hosts", "status", "--timeout-s", "1.25")
        self.assertEqual(code, 0)
        get_hosts.assert_called_once_with(host_name=None, timeout_s=1.25, long=False, json_output=False)

        with patch(
            "cli.automa_cli.hosts.runs.commands.start_host_automation_background",
            return_value=StartResult(0, "error"),
        ) as run_automation:
            code, _, _ = _invoke(
                "hosts",
                "run",
                "--name",
                "chase-sim-chaser",
                "--timeout-s",
                "1.25",
            )
        self.assertEqual(code, 0)
        self.assertEqual(run_automation.call_args.kwargs["timeout_s"], 1.25)

        for command, target in STATUS_COMMANDS.items():
            with self.subTest(command=command), patch(
                target,
                return_value=SimpleNamespace(exit_code=0, message="ok")
                if command[0] == "hosts"
                else {"vehicles": [{"provider": "chase-sim", "url": "http://localhost:5050/", "state": "running"}]},
            ) as get_status:
                code, _, _ = _invoke(*command)
            self.assertEqual(code, 0)
            self.assertEqual(get_status.call_args.kwargs["timeout_s"], 5.0)

        with patch(
            "cli.automa_cli.hosts.runs.commands.start_host_automation_background",
            return_value=StartResult(0, "error"),
        ) as run_automation:
            code, _, _ = _invoke(
                "hosts",
                "run",
                "--name",
                "chase-sim-chaser",
            )
        self.assertEqual(code, 0)
        self.assertEqual(run_automation.call_args.kwargs["timeout_s"], 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
