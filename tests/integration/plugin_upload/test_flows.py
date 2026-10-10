"""Public CLI -> live HTTP catalog -> viewer/replay/vehicle lifecycle flows."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.activation import step_activation_from_payload
from autonomy.decision_cycle.steps import decision_steps
from autonomy.plugins import uploaded_source
from autonomy.runtime.plugin_catalog import PluginCatalogAPI
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.plugin_catalog import PluginCatalogClient
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.cli.workbench_fixtures import (
    ImageReplayRunner, _wait_until, image_source, perception_activations,
    post_action, serve_workbench,
)
from tests.integration.automation_pipeline.pipeline_fixtures import chase_runtime
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
            # An unavailable dependency is not an upload gate.
            sources = [b"from not_uploaded_yet import Helper\n", b"raise RuntimeError('loaded later')\n"]
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
                        "vehicles", "plugins", "status", "--url", base, "--step", "perception", "--json",
                    ).stdout)
                    self.assertEqual(listing["outcome"]["status"], "ok")
                    self.assertEqual(listing["url"], base)
                    self.assertTrue(all(plugin["step"] == "perception" for plugin in listing["plugins"]))
                    registered = next(p for p in listing["plugins"] if p["id"] == plugin_id)
                    self.assertEqual(registered, receipt["plugin"])
                    self.assertEqual(uploaded_source(registered["metadata"]["source_file"]).read_bytes(), source)
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
                               "active_plugin_ids": ["prototype", "frame"]})
            post_action(base, {"action": "start"})
            _wait_until(lambda: runner.state()["current_frame"] is not None)
            self.assertTrue(first_marker.exists())
            post_action(base, {"action": "pause", "run_id": runner.state()["run_id"]})
            before = get_json(base, "/api/state")
            self.assertEqual([item["plugin_id"] for item in before["steps"]["perception"]["plugin_runs"]],
                             ["prototype", "frame"])
            prototype(file, next_marker, 2)
            _, second = upload(base, file, "prototype")
            self.assertNotEqual(first["plugin"]["metadata"]["revision"], second["plugin"]["metadata"]["revision"])
            self.assertFalse(next_marker.exists())
            self.assertEqual(uploaded_source(first["plugin"]["metadata"]["source_file"]).read_bytes(), first_source)
            shown = get_json(base, "/api/state")
            self.assertEqual(shown["current_frame"], before["current_frame"])
            self.assertEqual(shown["steps"], before["steps"])
            registered = next(p for p in shown["perception_plugin_catalog"]["plugins"] if p["id"] == "prototype")
            self.assertEqual(registered["revision"], second["plugin"]["metadata"]["revision"])
            post_action(base, {"action": "step", "run_id": shown["run_id"]})
            self.assertFalse(next_marker.exists())
            self.assertEqual(runner.state()["active_perception_plugin_ids"], ["prototype", "frame"])

    def test_catalog_updates_and_reselection_preserve_configs_and_report_matching_presets(self):
        with image_source(3) as root:
            staged = {}
            for step, preset in (("perception", "obstruction_observer"), ("memory", "recency_ledger")):
                staged[step] = json.loads(run_automa(
                    "vehicles", "update", step, "--id", "chase-sim-chaser", "--preset", preset,
                    "--dry-run", "--json", runtime_root=root / "vehicles",
                ).stdout)["manifest"]
            # A locally customized staged config must survive an unchanged selection.
            staged["perception"]["plugin_configs"]["multi_obstruction_tracks"]["floor_cutoff_y"] = 0.88
            runner = ImageReplayRunner(root, cadence_ms=30000, loop=True, activations={
                step: step_activation_from_payload(manifest) for step, manifest in staged.items()
            })
            base = serve_workbench(self, runner)
            catalog = json.loads(run_automa(
                "vehicles", "plugins", "status", "--url", base, "--step", "perception", "--json",
            ).stdout)
            default_cutoff = next(item for item in catalog["plugins"] if item["id"] == "multi_obstruction_tracks")["config"]["floor_cutoff_y"]
            file = root / "unselected.py"
            file.write_text("raise RuntimeError('not loaded during catalog registration')\n")
            upload(base, file, "unselected")
            ids = staged["perception"]["plugins"]
            post_action(base, {"action": "select_plugins", "step": "perception", "active_plugin_ids": ids})
            post_action(base, {"action": "start"})
            _wait_until(lambda: runner.state()["current_frame"] is not None)
            before = post_action(base, {"action": "pause", "run_id": runner.state()["run_id"]})["state"]
            run_id = before["run_id"]

            def cutoff(state):
                return state["steps"]["perception"]["measurements"]["multi_obstruction_tracks"]["floor_cutoff_y"]

            self.assertEqual(cutoff(before), 0.88)
            same = post_action(base, {"action": "select_plugins", "run_id": run_id,
                                     "step": "perception", "active_plugin_ids": ids})["state"]
            self.assertEqual(same["current_frame"], before["current_frame"])
            self.assertEqual(same["steps"], before["steps"])
            self.assertEqual(same["machine_detail"]["pipeline"]["perception_preset"], "obstruction_observer")
            # A changed selection then restores catalog defaults, while preserving the displayed frame.
            post_action(base, {"action": "select_plugins", "run_id": run_id,
                               "step": "perception", "active_plugin_ids": ["frame"]})
            restored = post_action(base, {"action": "select_plugins", "run_id": run_id,
                                         "step": "perception", "active_plugin_ids": ids})["state"]
            self.assertEqual(cutoff(restored), default_cutoff)
            self.assertEqual(restored["current_frame"]["frame_id"], before["current_frame"]["frame_id"])
            self.assertEqual([item["plugin_id"] for item in restored["steps"]["perception"]["plugin_runs"]], ids)
            post_action(base, {"action": "select_plugins", "run_id": run_id,
                               "step": "memory", "active_plugin_ids": []})
            memory = post_action(base, {"action": "select_plugins", "run_id": run_id,
                                       "step": "memory", "active_plugin_ids": staged["memory"]["plugins"]})["state"]
            pipeline = memory["machine_detail"]["pipeline"]
            self.assertEqual(pipeline["memory_preset"], staged["memory"]["metadata"]["preset"])
            self.assertEqual(pipeline["memory_plugin_report"]["applied_plugin_ids"], staged["memory"]["plugins"])
            self.assertEqual(memory["phase"], "paused")
            self.assertEqual(memory["run_id"], run_id)

    def test_syntax_fails_the_upload_and_an_import_error_fails_when_replay_loads_it(self):
        with image_source(1) as root:
            runner = ImageReplayRunner(root)
            base = serve_workbench(self, runner)
            before = get_json(base, "/api/state")
            catalog = get_json(base, "/api/plugins")
            for ids in (["missing"], ["frame", "frame"]):
                with self.assertRaises(HTTPError) as rejected:
                    post_action(base, {"action": "select_plugins", "step": "perception", "active_plugin_ids": ids})
                with rejected.exception as response:
                    self.assertEqual(response.code, 422)
                    failure = json.load(response)
                    self.assertFalse(failure["ok"])
                    self.assertEqual(failure["boundary"], "plugin_catalog")
                    self.assertTrue(failure["message"])
                unchanged = get_json(base, "/api/state")
                self.assertEqual(unchanged["active_perception_plugin_ids"], before["active_perception_plugin_ids"])
                self.assertEqual(unchanged["steps"], before["steps"])
                self.assertEqual(get_json(base, "/api/plugins"), catalog)
            file = root / "broken.py"
            file.write_text("class Prototype:\n    invalid Python !!!\n")
            result, receipt = upload(base, file, "broken", check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("broken.py:2: invalid syntax", receipt["error"])
            self.assertEqual(get_json(base, "/api/plugins"), catalog)
            file.write_text("import not_installed_anywhere\n")
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
            self.assertIn(str(root / "missing.py"), receipt["outcome"]["recovery"])
            self.assertEqual(get_json(base, "/api/plugins"), before)
            file = root / "prototype.py"
            file.write_text("class Prototype:\n    pass\n")
            result, receipt = upload(base, file, "", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(receipt["ok"])
            self.assertEqual(receipt["status"], "failed")
            self.assertTrue(receipt["error"])
            self.assertEqual(get_json(base, "/api/plugins"), before)
            rejected = PluginCatalogClient(base)({"plugin_id": "prototype"})
            self.assertEqual(rejected["error"], "missing field: step")

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
                    self.assertIn(b"<h1>Plugins</h1>", response.read())

    def test_cli_vehicle_address_and_viewer_proxy_share_the_host_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / "vehicles"
            unavailable = run_automa("vehicles", "plugins", "status", "--id", "chase-sim-chaser",
                                     "--json", runtime_root=runtime_root, check=False)
            missing = json.loads(unavailable.stdout)
            self.assertNotEqual(unavailable.returncode, 0)
            self.assertEqual(missing["vehicle_id"], "chase-sim-chaser")
            self.assertEqual(missing["outcome"]["status"], "unavailable")
            self.assertEqual(missing["outcome"]["recovery"], "./cli/automa vehicles status --id chase-sim-chaser")
            bundle = controller_bundle_paths(runtime_root / "chase-sim-chaser")
            chase = self.enterContext(chase_runtime(runtime_root))
            viewer = RuntimeViewServer(vehicle_id="chase-sim-chaser", automation_dir=Path(bundle["runtime_dir"]) / "automation",
                                       port=0, plugin_catalog=PluginCatalogClient(chase.base_url)).start()
            self.addCleanup(viewer.stop)
            file = root / "prototype.py"
            file.write_text("from dependency_can_arrive_later import Prototype\n")
            result = run_automa("vehicles", "plugins", "upload", "--id", "chase-sim-chaser",
                                "--file", str(file), "--step", "memory", "--plugin-id", "prototype",
                                "--entrypoint", "prototype:Prototype", runtime_root=runtime_root)
            self.assertIn("Upload: uploaded", result.stdout)
            self.assertIn("Plugin: prototype", result.stdout)
            self.assertIn("Selection: unchanged", result.stdout)
            self.assertIn("Next: ./cli/automa vehicles plugins status --id chase-sim-chaser --step memory", result.stdout)
            listed = json.loads(run_automa(
                "vehicles", "plugins", "list", "--id", "chase-sim-chaser", "--step", "memory", "--json",
                runtime_root=runtime_root,
            ).stdout)
            self.assertTrue(listed["ok"])
            status = json.loads(run_automa(
                "vehicles", "plugins", "status", "--id", "chase-sim-chaser", "--step", "memory", "--json",
                runtime_root=runtime_root,
            ).stdout)
            self.assertEqual(listed, status)
            self.assertEqual(len(listed["plugins"]), 1)
            self.assertEqual(listed["plugins"][0]["id"], "prototype")
            self.assertIn(listed["plugins"][0]["metadata"]["revision"], result.stdout)
            self.assertEqual(get_json(viewer.url, "/api/plugins"), get_json(chase.base_url, "/api/plugins"))
            self.assertEqual(chase.loop.host.catalog.resolve("memory", "prototype").metadata["revision"],
                             listed["plugins"][0]["metadata"]["revision"])

    def test_uploads_last_for_the_host_run_the_cli_names(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory) / "vehicles"
            file = Path(directory) / "prototype.py"
            file.write_text("from dependency_can_arrive_later import Prototype\n")

            def status(host_run_id: str) -> str:
                result = run_automa("vehicles", "plugins", "status", "--id", "chase-sim-chaser",
                                    "--step", "memory", runtime_root=runtime_root)
                self.assertIn(f"(host run {host_run_id})", result.stdout)
                return result.stdout

            with chase_runtime(runtime_root) as first:
                uploaded = run_automa("vehicles", "plugins", "upload", "--id", "chase-sim-chaser",
                                      "--file", str(file), "--step", "memory", "--plugin-id", "prototype",
                                      "--entrypoint", "prototype:Prototype", runtime_root=runtime_root)
                self.assertIn(f"(host run {first.loop.run_id})", uploaded.stdout)
                self.assertIn("memory/prototype", status(first.loop.run_id))
            with chase_runtime(runtime_root) as restarted:
                self.assertNotEqual(restarted.loop.run_id, first.loop.run_id)
                self.assertNotIn("memory/prototype", status(restarted.loop.run_id))
