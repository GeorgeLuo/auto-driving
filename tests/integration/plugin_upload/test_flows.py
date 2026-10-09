"""Public CLI -> live HTTP catalog -> viewer/replay/vehicle lifecycle flows."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import urlopen

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.plugin_catalog import PluginCatalogAPI
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.plugin_upload import PluginCatalogClient
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.cli.workbench_fixtures import (
    ImageReplayRunner, _wait_until, image_source, perception_activations,
    post_action, serve_workbench,
)
from tests.support.cli_runner import AUTOMA_PATH, run_automa


def get_json(base: str, route: str) -> dict:
    with urlopen(base.rstrip("/") + route, timeout=3) as response:
        return json.load(response)


def upload(base: str, file: Path, plugin_id: str, *, step: str = "perception", check=True):
    result = run_automa(
        "vehicles", "plugins", "upload", "--url", base, "--file", str(file),
        "--step", step, "--plugin-id", plugin_id, "--entrypoint", "prototype:Prototype", "--json",
        check=check,
    )
    return result, json.loads(result.stdout)


def prototype(file: Path, marker: Path, revision: int) -> None:
    file.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
        "from implementations.decision_cycle.perception.plugins.frame.plugin import FrameObservationPlugin\n"
        "from dataclasses import replace\n"
        "class Prototype(FrameObservationPlugin):\n"
        "    plugin_id = 'prototype'\n"
        "    def perceive(self, inputs):\n"
        "        batch = super().perceive(inputs)\n"
        f"        return replace(batch, measurements={{**batch.measurements, 'revision': {revision}}})\n"
    )


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

    def test_sequential_dependency_uploads_in_every_workbench_phase(self):
        with image_source(2) as root:
            runner = ImageReplayRunner(root, activations=perception_activations("frame"),
                                       cadence_ms=30000, loop=True)
            base = serve_workbench(self, runner)
            guidance = run_automa("vehicles", "plugins", "help").stdout
            self.assertIn("list", guidance)
            self.assertIn("existing host or workbench catalog", guidance)
            self.assertIn("module", run_automa("vehicles", "plugins", "upload", "--help").stdout)
            file = root / "prototype.py"
            # Neither an unavailable dependency nor invalid Python is an upload gate.
            sources = [b"from not_uploaded_yet import Helper\n", b"not valid Python !!!\n"]
            for phase in ("idle", "running", "paused", "completed"):
                if phase == "running":
                    post_action(base, {"action": "start"})
                    _wait_until(lambda: runner.state()["current_frame"] is not None)
                elif phase == "paused":
                    post_action(base, {"action": "pause", "run_id": runner.state()["run_id"]})
                elif phase == "completed":
                    post_action(base, {"action": "reset", "run_id": runner.state()["run_id"]})
                    post_action(base, {"action": "start", "loop": False, "cadence_ms": 1})
                    self.assertEqual(runner.wait(5)["phase"], "completed")
                before = get_json(base, "/api/state")
                previous = get_json(base, "/api/plugins")["catalog_version"]
                for index, source in enumerate(sources):
                    file.write_bytes(source)
                    plugin_id = f"{phase}-{index}"
                    result, receipt = upload(base, file, plugin_id)
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(receipt["status"], "uploaded")
                    self.assertGreater(receipt["catalog_version"], previous)
                    previous = receipt["catalog_version"]
                    listing = json.loads(run_automa(
                        "vehicles", "plugins", "list", "--url", base, "--step", "perception", "--json",
                    ).stdout)
                    self.assertTrue(all(plugin["step"] == "perception" for plugin in listing["plugins"]))
                    registered = next(p for p in listing["plugins"] if p["id"] == plugin_id)
                    self.assertEqual(registered, receipt["plugin"])
                    self.assertEqual(Path(registered["metadata"]["source_path"]).read_bytes(), source)
                shown = get_json(base, "/api/state")
                self.assertEqual(shown["phase"], phase)
                self.assertEqual(shown["run_id"], before["run_id"])
                self.assertEqual(shown["active_perception_plugin_ids"], before["active_perception_plugin_ids"])
                self.assertEqual(shown["current_frame"], before["current_frame"])
                self.assertEqual(shown["steps"], before["steps"])
                self.assertNotEqual(shown["perception_plugin_catalog"]["digest"],
                                    before["perception_plugin_catalog"]["digest"])
                ids = [p["id"] for p in shown["perception_plugin_catalog"]["plugins"]]
                self.assertIn(f"{phase}-0", ids)
                self.assertIn(f"{phase}-1", ids)

    def test_new_revision_leaves_running_source_and_selection_pinned(self):
        with image_source(3) as root:
            runner = ImageReplayRunner(root, cadence_ms=30000, loop=True)
            base = serve_workbench(self, runner)
            file, first_marker, next_marker = root / "prototype.py", root / "first", root / "next"
            prototype(file, first_marker, 1)
            first_source = file.read_bytes()
            _, first = upload(base, file, "prototype")
            self.assertFalse(first_marker.exists())
            post_action(base, {"action": "select_plugins", "step": "perception",
                               "active_plugin_ids": ["prototype"]})
            post_action(base, {"action": "start"})
            _wait_until(lambda: runner.state()["current_frame"] is not None)
            self.assertTrue(first_marker.exists())
            post_action(base, {"action": "pause", "run_id": runner.state()["run_id"]})
            before = get_json(base, "/api/state")
            prototype(file, next_marker, 2)
            _, second = upload(base, file, "prototype")
            self.assertNotEqual(first["plugin"]["metadata"]["revision"], second["plugin"]["metadata"]["revision"])
            self.assertFalse(next_marker.exists())
            self.assertEqual(Path(first["plugin"]["metadata"]["source_path"]).read_bytes(), first_source)
            shown = get_json(base, "/api/state")
            self.assertEqual(shown["current_frame"], before["current_frame"])
            self.assertEqual(shown["steps"], before["steps"])
            registered = next(p for p in shown["perception_plugin_catalog"]["plugins"] if p["id"] == "prototype")
            self.assertEqual(registered["revision"], second["plugin"]["metadata"]["revision"])
            post_action(base, {"action": "step", "run_id": shown["run_id"]})
            self.assertFalse(next_marker.exists())
            self.assertEqual(runner.state()["active_perception_plugin_ids"], ["prototype"])

    def test_invalid_code_succeeds_at_upload_and_fails_when_replay_loads_it(self):
        with image_source(1) as root:
            runner = ImageReplayRunner(root)
            base = serve_workbench(self, runner)
            file = root / "broken.py"
            file.write_text("invalid Python !!!")
            _, receipt = upload(base, file, "broken")
            self.assertTrue(receipt["ok"])
            post_action(base, {"action": "select_plugins", "step": "perception", "active_plugin_ids": ["broken"]})
            result = post_action(base, {"action": "start"})["state"]
            self.assertEqual(result["phase"], "failed")
            self.assertIsNotNone(result["failure"])
            self.assertIn("broken", [p["id"] for p in get_json(base, "/api/plugins")["plugins"]])

    def test_uploader_reports_failure_without_changing_catalog(self):
        with image_source(1) as root:
            base = serve_workbench(self, ImageReplayRunner(root))
            before = get_json(base, "/api/plugins")
            result, receipt = upload(base, root / "missing.py", "missing", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["status"], "failed")
            self.assertTrue(receipt["error"])
            self.assertEqual(get_json(base, "/api/plugins"), before)
            file = root / "prototype.py"
            file.write_text("not checked during upload")
            result, receipt = upload(base, file, "", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["status"], "failed")
            self.assertTrue(receipt["error"])
            self.assertEqual(get_json(base, "/api/plugins"), before)

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
                view = RuntimeViewServer(vehicle_id="chase-sim-chaser", automation_dir=root / "automation",
                                         port=0, plugin_catalog=PluginCatalogAPI(host.catalog)).start()
                self.addCleanup(view.stop)
                file = root / "prototype.py"
                file.write_text("raise RuntimeError('must not import on upload')")
                for index, phase in enumerate(("stopped", "running", "completed")):
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
                with urlopen(view.url + "plugins") as response:
                    self.assertIn(b"Plugin catalog", response.read())

    def test_cli_vehicle_address_and_viewer_proxy_share_the_host_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / "vehicles"
            bundle = controller_bundle_paths(runtime_root / "chase-sim-chaser")
            host = create_picar_host(steps=decision_steps())
            self.addCleanup(host.close)
            onboard = RuntimeViewServer(vehicle_id="picar", automation_dir=root / "onboard", port=0,
                                        plugin_catalog=PluginCatalogAPI(host.catalog)).start()
            self.addCleanup(onboard.stop)
            viewer = RuntimeViewServer(vehicle_id="chase-sim-chaser", automation_dir=Path(bundle["runtime_dir"]) / "automation",
                                       port=0, plugin_catalog=PluginCatalogClient(onboard.url)).start()
            self.addCleanup(viewer.stop)
            file = root / "prototype.py"
            file.write_text("dependency can arrive later")
            result = run_automa("vehicles", "plugins", "upload", "--id", "chase-sim-chaser",
                                "--file", str(file), "--step", "memory", "--plugin-id", "prototype",
                                "--entrypoint", "prototype:Prototype", runtime_root=runtime_root)
            self.assertIn("Uploaded memory/prototype revision", result.stdout)
            self.assertIn("Verify: automa vehicles plugins list --id chase-sim-chaser --step memory", result.stdout)
            listed = json.loads(run_automa(
                "vehicles", "plugins", "list", "--id", "chase-sim-chaser", "--step", "memory", "--json",
                runtime_root=runtime_root,
            ).stdout)
            self.assertTrue(listed["ok"])
            self.assertEqual(len(listed["plugins"]), 1)
            self.assertEqual(listed["plugins"][0]["id"], "prototype")
            self.assertIn(listed["plugins"][0]["metadata"]["revision"], result.stdout)
            self.assertEqual(get_json(viewer.url, "/api/plugins"), get_json(onboard.url, "/api/plugins"))
            self.assertEqual(host.catalog.resolve("memory", "prototype").metadata["revision"],
                             listed["plugins"][0]["metadata"]["revision"])
