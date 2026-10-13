"""Automation run treats every decision-cycle step by one rule."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.steps import builtin_activation
from cli.automa_cli.app import main
from cli.automa_cli.automation import run_vehicle_automation
from tests.support.runtime_fixtures import write_json

VEHICLE = "chase-sim-chaser"
UNSTAGED = {step: "builtin" if builtin_activation(step) else "none" for step in STEPS}
UNAVAILABLE_VIEW = {"schema": "automa_perception_view_v1", "available": False, "status": "unavailable", "url": None}


def _invoke(*args: str) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
        code = main(list(args))
    return code, stdout.getvalue()


class StepFairnessTests(unittest.TestCase):
    @contextlib.contextmanager
    def _runtime(self, *staged: str, released: bool = True) -> Iterator[Path]:
        """A vehicle runtime with ``staged`` steps updated. An update builds a
        release; with nothing staged, ``released`` writes one."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("cli.automa_cli.app.VEHICLES_RUNTIME_ROOT", root), mock.patch(
                "cli.automa_cli.runtime_hosts.RUNTIME_ROOT", root
            ), mock.patch(
                "cli.automa_cli.automation.get_perception_view_status", return_value=dict(UNAVAILABLE_VIEW)
            ):
                for step in staged:
                    code, stdout = _invoke("vehicles", "update", step, "--id", VEHICLE, "--json")
                    self.assertEqual(code, 0, stdout)
                (root / VEHICLE / "bundle").mkdir(parents=True, exist_ok=True)
                if released and not staged:
                    write_json(
                        root / VEHICLE / "bundle/releases/latest-controller-bundle.json",
                        {"schema": "automa_controller_bundle_manifest_v0", "tree_sha256": "test"},
                    )
                yield root

    def test_a_run_starts_with_any_single_step_staged(self) -> None:
        """Regression: a run was refused unless perception was staged, and it
        passed perception apart from the memory and proposal activations."""

        for step in STEPS:
            with self.subTest(step=step), self._runtime(step), mock.patch(
                "cli.automa_cli.automation.runtime_base_url", return_value="http://host.invalid"
            ), mock.patch(
                "cli.automa_cli.automation.monitor_runtime", return_value=(0, "done")
            ) as monitor:
                result = run_vehicle_automation(
                    vehicle_id=VEHICLE, take_control=False, num_decisions=1, output=io.StringIO()
                )
                self.assertEqual((result.exit_code, result.message), (0, "done"))
                selections = monitor.call_args.kwargs["selections"]
                self.assertEqual(list(selections), list(STEPS))
                self.assertEqual(
                    {name: item["source"] for name, item in selections.items()},
                    {**UNSTAGED, step: "staged"},
                )


if __name__ == "__main__":
    unittest.main()
