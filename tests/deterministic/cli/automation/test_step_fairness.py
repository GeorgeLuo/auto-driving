"""Host run and status treat every decision-cycle step by one rule."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from tests.support.runtime_fixtures import write_host_fixture
from unittest import mock

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.steps import builtin_activation
from cli.automa_cli.hosts.automation import run_host_automation
from cli.automa_cli.hosts.automation_status import collect_automation_status
from tests.shared.cli.invoke import invoke_automa
from tests.support.runtime_fixtures import stage_step, write_json

VEHICLE = "chase-sim-chaser"
UNSTAGED = {step: "builtin" if builtin_activation(step) else "none" for step in STEPS}
UNAVAILABLE_VIEW = {"schema": "automa_perception_view", "available": False, "status": "unavailable", "url": None}


def _invoke(*args: str) -> tuple[int, str]:
    result = invoke_automa(*args, check=False)
    return result.returncode, result.stdout


class StepFairnessTests(unittest.TestCase):
    @contextlib.contextmanager
    def _runtime(self, *staged: str, released: bool = True) -> Iterator[Path]:
        """A vehicle runtime with ``staged`` steps written with a packaged
        release; with nothing staged, ``released`` writes one."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_host_fixture(root, VEHICLE)
            with mock.patch("cli.automa_cli.hosts.runtime_hosts.RUNTIME_ROOT", root), mock.patch(
                "cli.automa_cli.hosts.automation_status.get_perception_view_status", return_value=dict(UNAVAILABLE_VIEW)
            ):
                for step in staged:
                    stage_step(root, VEHICLE, step)
                (root / VEHICLE / "bundle").mkdir(parents=True, exist_ok=True)
                if released and not staged:
                    write_json(
                        root / VEHICLE / "bundle/releases/latest-controller-bundle.json",
                        {"schema": "automa_controller_bundle_manifest_v0", "tree_sha256": "test"},
                    )
                yield root

    def _status(self) -> dict:
        (status,) = collect_automation_status(host_name=VEHICLE, view_timeout_s=0)
        return status

    def test_a_run_starts_with_any_single_step_staged(self) -> None:
        """Regression: a run was refused unless perception was staged, and it
        passed perception apart from the memory and proposal activations."""

        for step in STEPS:
            with self.subTest(step=step), self._runtime(step), mock.patch(
                "cli.automa_cli.hosts.automation.runtime_base_url", return_value="http://host.invalid"
            ), mock.patch(
                "cli.automa_cli.hosts.automation.monitor_runtime", return_value=(0, "done")
            ) as monitor:
                result = run_host_automation(
                    host_name=VEHICLE, take_control=False, num_decisions=1, output=io.StringIO()
                )
                self.assertEqual((result.exit_code, result.message), (0, "done"))
                selections = monitor.call_args.kwargs["selections"]
                self.assertEqual(list(selections), list(STEPS))
                self.assertEqual(
                    {name: item["source"] for name, item in selections.items()},
                    {**UNSTAGED, step: "staged"},
                )

    def test_status_reports_every_step_by_where_its_selection_comes_from(self) -> None:
        """Regression: status reported perception's staged document and the
        decision steps' plugins, and never memory or observation."""

        for step in STEPS:
            with self.subTest(step=step), self._runtime(step):
                status = self._status()
                code, human = _invoke("hosts", "status", "--long")
            self.assertEqual(code, 0, human)
            steps = status["steps"]
            self.assertEqual({name: item["source"] for name, item in steps.items()}, {**UNSTAGED, step: "staged"})
            self.assertIn("  steps: staged\n", human)
            for name, item in steps.items():
                self.assertEqual(set(item), {"activation", "source", "preset", "plugins"})
                self.assertTrue(item["activation"].endswith(f"bundle/runtime/{name}/active.json"))
                self.assertIn(f"\n    {name}: ", human)
            self.assertNotIn("\n  perception:", human)

    def test_status_reports_every_step_as_the_running_host_applied_it(self) -> None:
        """Regression: status showed perception's staged document while the
        host ran, and the decision steps as the host applied them."""

        applied = {step: {"plugin_ids": [f"{step}_plugin"], "preset": f"{step}_preset", "last_error": None} for step in STEPS}
        with self._runtime("perception", "proposal") as root:
            automation = root / VEHICLE / "bundle/runtime/automation"
            write_json(automation / "process.json", {"pid": os.getpid()})
            write_json(automation / "state.json", {"pid": os.getpid(), "status": "running", "steps": {**applied, "cycle_count": 3}})
            status = self._status()
            code, human = _invoke("hosts", "status", "--long")

        self.assertEqual(code, 0, human)
        self.assertEqual(list(status["steps"]), list(STEPS))
        for step, item in status["steps"].items():
            self.assertEqual(
                (item["source"], item["preset"], item["plugins"]), ("applied", f"{step}_preset", [f"{step}_plugin"])
            )
            self.assertIn(f"\n    {step}: {step}_preset ({step}_plugin)\n", human)
        self.assertIn("  steps: applied by the running host\n", human)

    def test_deployed_needs_a_release_and_no_particular_step(self) -> None:
        """Regression: a vehicle was deployed only once perception was staged."""

        for staged in ((), ("memory",), ("proposal",), ("perception",)):
            with self.subTest(staged=staged), self._runtime(*staged):
                self.assertTrue(self._status()["deployed"])
        with self._runtime(released=False):
            self.assertFalse(self._status()["deployed"])


if __name__ == "__main__":
    unittest.main()
