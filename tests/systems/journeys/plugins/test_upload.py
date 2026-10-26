"""Public CLI -> live HTTP catalog -> viewer/replay/vehicle lifecycle flows."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.plugin_catalog import PluginCatalogAPI
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import get_json
from tests.support.cli_runner import AUTOMA_PATH


def upload(base: str, file: Path, plugin_id: str, *, step: str = "perception", check=True):
    result = invoke_automa(
        "host", "plugins", "upload", "--url", base, "--file", str(file),
        "--step", step, "--plugin-id", plugin_id, "--entrypoint", "prototype:Prototype", "--json",
        check=check,
    )
    return result, json.loads(result.stdout)


class PluginUploadFlows(unittest.TestCase):
    def test_missing_python_environment_has_actionable_cli_signal(self):
        result = subprocess.run(
            [sys.executable, "-S", str(AUTOMA_PATH), "help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Python module", result.stderr)
        self.assertIn("<environment>/bin/python cli/automa", result.stderr)
        self.assertIn("README.md#setup", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_vehicle_catalog_uploads_before_during_and_after_run_completion(self):
        browser_runtime = sync_playwright().start()
        self.addCleanup(browser_runtime.stop)
        browser = browser_runtime.chromium.launch()
        self.addCleanup(browser.close)
        for vehicle in ("chase", "picar"):
            with self.subTest(vehicle=vehicle), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                steps = decision_steps()
                host = (create_chase_host(ChaseSimCar(), steps=steps) if vehicle == "chase"
                        else create_picar_host(steps=steps))
                self.addCleanup(host.close)
                host.follow_activations({}, root / "runtime")
                identity = host.applied_decision()
                view = RuntimeViewServer(host_id="chase-sim-chaser", automation_dir=root / "automation",
                                         port=0, plugin_catalog=PluginCatalogAPI(host.catalog)).start()
                self.addCleanup(view.stop)
                file = root / "prototype.py"
                file.write_text("raise RuntimeError('must not import on upload')")
                page = browser.new_page()
                page.goto(view.url + "plugins")
                page.get_by_role("checkbox", name="Show revisions").check()
                for index, phase in enumerate(("pre_start", "running", "completed")):
                    if phase == "running":
                        host.start(RunConfiguration(mode="observe_only", num_decisions=1))
                    elif phase == "completed":
                        host.run(DecisionFrameContext("frame-1", 1, int(time.time() * 1000)))
                    self.assertEqual(host.session_status()["status"], phase)
                    before_steps = host.status()["steps"]
                    _, receipt = upload(view.url, file, f"prototype-{index}", step="proposal")
                    self.assertTrue(receipt["ok"])
                    self.assertEqual(host.applied_decision(), identity)
                    self.assertEqual(host.status()["steps"], before_steps)
                    self.assertEqual(host.session_status()["status"], phase)
                    self.assertIn(f"prototype-{index}", [p["id"] for p in get_json(view.url, "/api/plugins")["plugins"]])
                    row = page.locator("tbody tr").filter(has=page.get_by_role("cell", name=f"prototype-{index}", exact=True))
                    expect(row).to_have_count(1)
                    expect(row.get_by_role("cell", name=receipt["plugin"]["metadata"]["revision"], exact=True)).to_be_visible()
