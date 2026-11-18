"""Public CLI -> live HTTP catalog -> vehicle lifecycle flows."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.session import RunConfiguration
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import get_json
from tests.shared.integration.plugin_flow_fixtures import served_routes
from tests.support.cli_runner import AUTOMA_PATH


def upload(base: str, file: Path, plugin_id: str, *, step: str = "perception", check=True):
    result = invoke_automa(
        "plugins", "upload", "--url", base, "--file", str(file),
        "--name", f"{step}/{plugin_id}", "--entrypoint", "prototype:Prototype", "--json",
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
        for vehicle in ("chase", "picar"):
            with self.subTest(vehicle=vehicle), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                steps = decision_steps()
                host = (create_chase_host(ChaseSimCar(), steps=steps) if vehicle == "chase"
                        else create_picar_host(steps=steps))
                self.addCleanup(host.close)
                host.follow_activations({}, root / "runtime")
                identity = host.applied_decision()
                url = self.enterContext(served_routes(host, vehicle))
                file = root / "prototype.py"
                file.write_text("raise RuntimeError('must not import on upload')")
                for index, phase in enumerate(("pre_start", "running", "completed")):
                    if phase == "running":
                        host.start(RunConfiguration(mode="observe_only", num_decisions=1))
                    elif phase == "completed":
                        host.run(DecisionFrameContext("frame-1", 1, int(time.time() * 1000)))
                    self.assertEqual(host.session_status()["status"], phase)
                    before_steps = host.status()["steps"]
                    _, receipt = upload(url, file, f"prototype-{index}", step="proposal")
                    self.assertTrue(receipt["ok"])
                    self.assertEqual(host.applied_decision(), identity)
                    self.assertEqual(host.status()["steps"], before_steps)
                    self.assertEqual(host.session_status()["status"], phase)
                    listed = {plugin["id"]: plugin for plugin in get_json(url, "/api/plugins")["plugins"]}
                    self.assertEqual(listed[f"prototype-{index}"]["metadata"]["revision"],
                                     receipt["plugin"]["metadata"]["revision"])
