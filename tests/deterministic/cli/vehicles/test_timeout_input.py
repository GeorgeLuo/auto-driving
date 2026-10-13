from __future__ import annotations

import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from autonomy.decision_cycle.activation import STEPS
from cli.automa_cli.app import main


INVALID_TIMEOUTS = ("0", "-1", "nan", "-nan", "inf", "+inf", "-inf")
# Every `vehicles update <step>` command dispatches to the shared update.
UPDATE_STEP = "cli.automa_cli.app.update_vehicle_step"
TIMEOUT_MESSAGE = "--timeout-s must be a finite number greater than zero"


def _invoke(*args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


def _timeout_args(value: str) -> tuple[str, ...]:
    if value in {"-nan", "-inf"}:
        return (f"--timeout-s={value}",)
    return ("--timeout-s", value)


class TimeoutInputTests(unittest.TestCase):
    def test_invalid_status_values_use_one_json_error_envelope_before_status_work(self) -> None:
        for value in INVALID_TIMEOUTS:
            with self.subTest(value=value), patch("cli.automa_cli.app.get_vehicle_status") as get_status:
                code, stdout, stderr = _invoke(
                    "vehicles",
                    "status",
                    *_timeout_args(value),
                    "--json",
                )

            self.assertEqual(code, 2)
            self.assertEqual(stderr, "")
            self.assertNotIn("Traceback", stdout)
            payload = json.loads(stdout)
            self.assertEqual(payload["schema"], "automa_cli_error_v1")
            self.assertEqual(payload["error"], "timeout_invalid")
            self.assertEqual(payload["layer"], "input")
            self.assertEqual(payload["exit_code"], 2)
            self.assertEqual(payload["details"]["argument"], "--timeout-s")
            self.assertIn(TIMEOUT_MESSAGE, payload["message"])
            get_status.assert_not_called()

    def test_invalid_status_values_use_the_existing_human_error_channel(self) -> None:
        for value in INVALID_TIMEOUTS:
            with self.subTest(value=value), patch("cli.automa_cli.app.get_vehicle_status") as get_status:
                code, stdout, stderr = _invoke(
                    "vehicles",
                    "status",
                    *_timeout_args(value),
                )

            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, f"automa vehicles status: {TIMEOUT_MESSAGE}\n")
            get_status.assert_not_called()

    def test_invalid_automation_values_stop_before_worker_dispatch(self) -> None:
        for value in INVALID_TIMEOUTS:
            with self.subTest(value=value):
                with (
                    patch("cli.automa_cli.app.run_vehicle_automation") as run_automation,
                    patch("cli.automa_cli.app.start_vehicle_automation_background") as start_automation,
                    patch("cli.automa_cli.app.record_vehicle_automation_terminal_result") as record_result,
                ):
                    code, stdout, stderr = _invoke(
                        "vehicles",
                        "automation",
                        "run",
                        "--id",
                        "chase-sim-chaser",
                        *_timeout_args(value),
                        "--foreground",
                    )

                self.assertEqual(code, 2)
                self.assertIn("automa vehicles automation run", stdout)
                self.assertIn(TIMEOUT_MESSAGE, stdout)
                self.assertNotIn("Traceback", stdout)
                self.assertEqual(stderr, "")
                run_automation.assert_not_called()
                start_automation.assert_not_called()
                record_result.assert_not_called()

    def test_invalid_values_stop_every_step_update_before_local_staging(self) -> None:
        """Regression: a non-finite timeout reached discovery for a step that kept its own handler."""

        for step in STEPS:
            for value in INVALID_TIMEOUTS:
                with self.subTest(step=step, value=value), patch(UPDATE_STEP) as update:
                    code, stdout, stderr = _invoke(
                        "vehicles", "update", step, "--id", "chase-sim-chaser",
                        *_timeout_args(value), "--dry-run", "--json",
                    )

                self.assertEqual(code, 2)
                self.assertEqual(stderr, "")
                payload = json.loads(stdout)
                self.assertEqual(payload["schema"], "automa_cli_error_v1")
                self.assertEqual(payload["error"], "timeout_invalid")
                self.assertEqual(payload["exit_code"], 2)
                self.assertIn(f"automa vehicles update {step}", payload["message"])
                update.assert_not_called()

    def test_invalid_values_use_the_human_output_channel_for_every_step(self) -> None:
        for step in STEPS:
            with self.subTest(step=step), patch(UPDATE_STEP) as update:
                code, stdout, stderr = _invoke(
                    "vehicles", "update", step, "--id", "chase-sim-chaser", "--timeout-s", "0", "--dry-run",
                )

            self.assertEqual(code, 2)
            self.assertIn(f"automa vehicles update {step}", stdout)
            self.assertIn(TIMEOUT_MESSAGE, stdout)
            self.assertEqual(stderr, "")
            update.assert_not_called()

    def test_every_step_update_passes_the_discovery_timeout_to_staging(self) -> None:
        for step in STEPS:
            for args, expected in ((("--timeout-s", "1.25"), 1.25), ((), 5.0)):
                with self.subTest(step=step, args=args), patch(UPDATE_STEP, return_value=(0, "ok")) as update:
                    code, _, _ = _invoke(
                        "vehicles", "update", step, "--id", "chase-sim-chaser", *args, "--dry-run"
                    )
                self.assertEqual(code, 0)
                self.assertEqual(update.call_args.kwargs["timeout_s"], expected)

    def test_valid_positive_timeout_reaches_each_consumer_unchanged(self) -> None:
        with patch(
            "cli.automa_cli.app.get_vehicle_status",
            return_value={"vehicles": []},
        ) as get_status:
            code, _, _ = _invoke("vehicles", "status", "--timeout-s", "1.25")
        self.assertEqual(code, 0)
        get_status.assert_called_once_with(
            vehicle_id=None,
            chase_url=None,
            chase_ws_url=None,
            timeout_s=1.25,
        )

        with patch(
            "cli.automa_cli.app.run_vehicle_automation",
            return_value=SimpleNamespace(exit_code=0, message="ok"),
        ) as run_automation, patch(
            "cli.automa_cli.app.record_vehicle_automation_terminal_result"
        ):
            code, _, _ = _invoke(
                "vehicles",
                "automation",
                "run",
                "--id",
                "chase-sim-chaser",
                "--timeout-s",
                "1.25",
                "--foreground",
            )
        self.assertEqual(code, 0)
        self.assertEqual(run_automation.call_args.kwargs["timeout_s"], 1.25)

        with patch(
            "cli.automa_cli.app.get_vehicle_status",
            return_value={"vehicles": []},
        ) as get_status:
            code, _, _ = _invoke("vehicles", "status")
        self.assertEqual(code, 0)
        self.assertEqual(get_status.call_args.kwargs["timeout_s"], 5.0)

        with patch(
            "cli.automa_cli.app.run_vehicle_automation",
            return_value=SimpleNamespace(exit_code=0, message="ok"),
        ) as run_automation, patch(
            "cli.automa_cli.app.record_vehicle_automation_terminal_result"
        ):
            code, _, _ = _invoke(
                "vehicles",
                "automation",
                "run",
                "--id",
                "chase-sim-chaser",
                "--foreground",
            )
        self.assertEqual(code, 0)
        self.assertEqual(run_automation.call_args.kwargs["timeout_s"], 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
