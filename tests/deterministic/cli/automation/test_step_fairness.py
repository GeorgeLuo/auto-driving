"""Host run and status treat every decision-cycle step by one rule."""

from __future__ import annotations

import contextlib
import json
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
from cli.automa_cli.hosts.run_record import step_status
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
            self.assertEqual(status["applied_steps"], {"available": False, "reason": "host not running"})
            self.assertIn("  steps: staged; applied unavailable (host not running)\n", human)
            for name, item in steps.items():
                self.assertEqual(set(item), {"activation", "source", "saved", "preset", "plugins", "plugin_configs", "applied"})
                self.assertTrue(item["activation"].endswith(f"bundle/runtime/{name}/active.json"))
                self.assertIsNone(item["applied"])
                self.assertRegex(human, rf"\n    {name}: .+  source={item['source']}( \(default\))?\n")
            self.assertNotIn("\n  perception:", human)
            self.assertNotIn("applied:", human)

    def test_status_reports_every_step_staged_beside_how_the_running_host_applied_it(self) -> None:
        """Regression: status showed perception's staged document while the
        host ran, and the decision steps as the host applied them."""

        applied = {
            step: {"plugin_ids": [f"{step}_plugin"], "preset": f"{step}_preset", "run_count": 3,
                   "failure_count": 1, "last_error": f"{step} timed out"}
            for step in STEPS
        }
        with self._runtime("perception", "proposal") as root:
            automation = root / VEHICLE / "bundle/runtime/automation"
            write_json(automation / "process.json", {"pid": os.getpid()})
            write_json(automation / "state.json", {"pid": os.getpid(), "status": "running", "steps": {**applied, "cycle_count": 3}})
            status = self._status()
            code, human = _invoke("hosts", "status", "--long")

        self.assertEqual(code, 0, human)
        self.assertEqual(list(status["steps"]), list(STEPS))
        self.assertEqual(status["applied_steps"], {"available": True, "reason": None})
        self.assertIn("  steps: staged, and applied by the running host\n", human)
        for step, item in status["steps"].items():
            self.assertEqual(item["source"], {**UNSTAGED, "perception": "staged", "proposal": "staged"}[step])
            self.assertEqual(item["applied"], {
                "preset": f"{step}_preset", "plugins": [f"{step}_plugin"], "plugin_configs": None, "run_count": 3,
                "failure_count": 1, "last_error": f"{step} timed out", "error": None,
            })
            self.assertIn(
                f"\n      applied: {step}_preset ({step}_plugin)  runs=3  failures=1  last_error={step} timed out\n",
                human,
            )

    def test_status_says_as_staged_only_when_the_host_applies_the_staged_configs(self) -> None:
        """Regression: a restaged config the host had not applied read ``as staged``."""

        with self._runtime("memory") as root:
            staged = self._status()["steps"]["memory"]
            (plugin,) = staged["plugins"]
            automation = root / VEHICLE / "bundle/runtime/automation"
            write_json(automation / "process.json", {"pid": os.getpid()})
            for configs, label in (
                (staged["plugin_configs"], "as staged"),
                ({plugin: {**staged["plugin_configs"][plugin], "max_age_ms": 1}}, f"staged plugins, configs differ ({plugin})"),
                (None, "staged plugins, configs not reported"),
            ):
                with self.subTest(label=label):
                    report = {"plugin_ids": staged["plugins"], "preset": staged["preset"], "run_count": 2, "failure_count": 0}
                    if configs is not None:
                        report["plugin_configs"] = configs
                    write_json(automation / "state.json", {
                        "pid": os.getpid(), "status": "running", "steps": step_status({"steps": {"memory": report}}),
                    })
                    applied = self._status()["steps"]["memory"]["applied"]
                    code, human = _invoke("hosts", "status", "--long")
                    self.assertEqual(code, 0, human)
                    self.assertEqual(applied["plugin_configs"], configs)
                    self.assertIn(f"\n      applied: {label}  runs=2  failures=0\n", human)

    def test_status_says_when_the_host_could_not_report_a_step(self) -> None:
        reports = {"memory": {"status": "error", "error": "status() raised"}}
        with self._runtime("memory") as root:
            automation = root / VEHICLE / "bundle/runtime/automation"
            write_json(automation / "process.json", {"pid": os.getpid()})
            write_json(automation / "state.json", {"pid": os.getpid(), "status": "running", "steps": step_status({"steps": reports})})
            status = self._status()
            code, human = _invoke("hosts", "status", "--long")

        self.assertEqual(code, 0, human)
        self.assertEqual(status["steps"]["memory"]["applied"]["error"], "status() raised")
        self.assertRegex(human, r"\n    memory: .+  source=staged \(default\)\n      applied: unavailable \(status\(\) raised\)\n")

    def test_deployed_needs_a_release_and_no_particular_step(self) -> None:
        """Regression: a vehicle was deployed only once perception was staged."""

        for staged in ((), ("memory",), ("proposal",), ("perception",)):
            with self.subTest(staged=staged), self._runtime(*staged):
                self.assertTrue(self._status()["deployed"])
        with self._runtime(released=False):
            status = self._status()
            code, human = _invoke("hosts", "status", "--long")
        self.assertFalse(status["deployed"])
        self.assertIsNone(status["release"])
        self.assertIn(f"\n  release: none (run `./cli/automa hosts update --name {VEHICLE}` to package one)\n", human)

    def test_status_says_whether_plugins_save_saved_a_staged_selection(self) -> None:
        with self._runtime("memory", "proposal") as root:
            path = root / VEHICLE / "bundle/runtime/memory/active.json"
            activation = json.loads(path.read_text(encoding="utf-8"))
            activation["metadata"]["saved"] = True
            write_json(path, activation)
            status = self._status()
            code, human = _invoke("hosts", "status", "--long")

        self.assertEqual(code, 0, human)
        self.assertEqual((status["steps"]["memory"]["saved"], status["steps"]["proposal"]["saved"]), (True, False))
        self.assertRegex(human, r"\n    memory: .+  source=staged \(saved\)\n")
        self.assertRegex(human, r"\n    proposal: .+  source=staged \(default\)\n")


if __name__ == "__main__":
    unittest.main()
