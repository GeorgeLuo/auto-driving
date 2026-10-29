"""Public CLI -> live HTTP catalog -> viewer/replay/vehicle lifecycle flows."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from autonomy.decision_cycle.activation import step_activation_from_payload
from autonomy.plugins import uploaded_source
from cli.automa_cli.hosts.bundles import controller_bundle_paths
from cli.automa_cli.plugins.catalog import PluginCatalogClient
from implementations.decision_cycle.catalog import selection_activation
from cli.automa_cli.frontend.server import RuntimeViewServer
from tests.shared.cli.automation_pipeline.pipeline_fixtures import chase_runtime
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import (
    ImageReplayRunner,
    _wait_until,
    get_json,
    image_source,
    perception_activations,
    post_action,
    serve_workbench,
)


def upload(base: str, file: Path, plugin_id: str, *options: str, step: str = "perception", check=True):
    result = invoke_automa(
        "plugins", "upload", "--url", base, "--file", str(file),
        "--name", f"{step}/{plugin_id}", "--entrypoint", "prototype:Prototype", *options, "--json",
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
    def test_sequential_dependency_uploads_in_every_workbench_phase(self):
        with image_source(2) as root:
            runner = ImageReplayRunner(root, activations=perception_activations("frame"),
                                       cadence_ms=30000, loop=True)
            base = serve_workbench(self, runner)
            guidance = " ".join(invoke_automa("plugins", "upload", "--help").stdout.split())
            self.assertIn("plugins list", guidance)
            self.assertIn("existing host or workbench catalog", guidance)
            self.assertIn("module", guidance)
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
                    listing = json.loads(invoke_automa(
                        "plugins", "list", "--url", base, "--step", "perception", "--long", "--json",
                    ).stdout)
                    self.assertTrue(listing["ok"])
                    self.assertEqual(listing["url"], base)
                    # A workbench catalog takes uploads but arms nothing.
                    self.assertIsNone(listing["armed"])
                    self.assertEqual(listing["catalog"]["source"], base)
                    self.assertTrue(all(plugin["step"] == "perception" for plugin in listing["catalog"]["plugins"]))
                    registered = next(p for p in listing["catalog"]["plugins"] if p["id"] == plugin_id)
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
            staged = {
                step: selection_activation(step, preset=preset).to_payload()
                for step, preset in (("perception", "obstruction_observer"), ("memory", "recency_ledger"))
            }
            # A locally customized staged config must survive an unchanged selection.
            staged["perception"]["plugin_configs"]["multi_obstruction_tracks"]["floor_cutoff_y"] = 0.88
            runner = ImageReplayRunner(root, cadence_ms=30000, loop=True, activations={
                step: step_activation_from_payload(manifest) for step, manifest in staged.items()
            })
            base = serve_workbench(self, runner)
            catalog = json.loads(invoke_automa(
                "plugins", "list", "--url", base, "--step", "perception", "--long", "--json",
            ).stdout)["catalog"]
            default_cutoff = next(item for item in catalog["plugins"] if item["id"] == "multi_obstruction_tracks")["config"]["floor_cutoff_y"]
            file = root / "unselected.py"
            file.write_text("raise RuntimeError('not loaded during catalog registration')\n")
            upload(base, file, "unselected")
            ids = staged["perception"]["plugins"]
            post_action(base, {"action": "select_plugins", "step": "perception", "active_plugin_ids": ids})
            started = post_action(base, {"action": "start"})["state"]
            self.assertEqual(started["phase"], "running", started.get("failure"))
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
            unnamed = invoke_automa("plugins", "upload", "--url", base, "--file", str(file), "--name", "perception/",
                                    "--entrypoint", "prototype:Prototype", "--json", check=False)
            self.assertEqual((unnamed.returncode, unnamed.stdout), (2, ""))
            empty = PluginCatalogClient(base)({"step": "perception", "plugin_id": "", "entrypoint": "prototype:Prototype",
                                               "filename": file.name, "source_base64": base64.b64encode(file.read_bytes()).decode()})
            self.assertEqual((empty["ok"], empty["status"]), (False, "failed"))
            self.assertTrue(empty["error"])
            self.assertEqual(get_json(base, "/api/plugins"), before)
            rejected = PluginCatalogClient(base)({"plugin_id": "prototype"})
            self.assertEqual(rejected["error"], "missing field: step")

    def test_upload_stores_its_config_with_the_revision(self):
        with image_source(1) as root:
            base = serve_workbench(self, ImageReplayRunner(root))
            file = root / "prototype.py"
            file.write_text("class Prototype:\n    pass\n")
            before = get_json(base, "/api/plugins")
            for config in ("[1]", "{not json", "null"):
                with self.subTest(config=config):
                    result, receipt = upload(base, file, "prototype", "--config", config, check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse(receipt["ok"])
                    self.assertIn("--config", receipt["error"])
                    self.assertIn("--config", receipt["outcome"]["recovery"])
                    self.assertEqual(get_json(base, "/api/plugins"), before)
            _, receipt = upload(base, file, "prototype", "--config", '{"floor_cutoff_y": 0.9}')
            self.assertEqual(receipt["plugin"]["config"], {"floor_cutoff_y": 0.9})
            listed = next(item for item in get_json(base, "/api/plugins")["plugins"] if item["id"] == "prototype")
            self.assertEqual(listed["config"], {"floor_cutoff_y": 0.9})
            self.assertEqual(listed["metadata"]["revision"], receipt["plugin"]["metadata"]["revision"])

    def test_cli_vehicle_address_and_viewer_proxy_share_the_host_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / "vehicles"
            unavailable = invoke_automa("plugins", "list", "--host-name", "chase-sim-chaser", "--long",
                                        "--json", runtime_root=runtime_root, check=False)
            missing = json.loads(unavailable.stdout)
            self.assertNotEqual(unavailable.returncode, 0)
            self.assertEqual(missing["host_name"], "chase-sim-chaser")
            self.assertFalse(missing["ok"])
            # The error already says why a named host is unreachable; there is no retry hint.
            self.assertNotIn("Next:", invoke_automa("plugins", "list", "--host-name", "chase-sim-chaser",
                                                    runtime_root=runtime_root, check=False).stdout)
            bundle = controller_bundle_paths(runtime_root / "chase-sim-chaser")
            chase = self.enterContext(chase_runtime(runtime_root))
            viewer = RuntimeViewServer(host_name="chase-sim-chaser", automation_dir=Path(bundle["runtime_dir"]) / "automation",
                                       port=0, plugin_catalog=PluginCatalogClient(chase.base_url)).start()
            self.addCleanup(viewer.stop)
            file = root / "prototype.py"
            file.write_text("from dependency_can_arrive_later import Prototype\n")
            result = invoke_automa("plugins", "upload", "--host-name", "chase-sim-chaser",
                                   "--file", str(file), "--name", "memory/prototype",
                                   "--entrypoint", "prototype:Prototype", runtime_root=runtime_root)
            self.assertIn("Upload: uploaded", result.stdout)
            self.assertIn("Plugin: prototype", result.stdout)
            self.assertIn("Selection: unchanged", result.stdout)
            self.assertIn("Next: ./cli/automa plugins list --host-name chase-sim-chaser --step memory --long", result.stdout)
            armed = json.loads(invoke_automa(
                "plugins", "list", "--host-name", "chase-sim-chaser", "--step", "memory", "--json",
                runtime_root=runtime_root,
            ).stdout)
            self.assertTrue(armed["ok"])
            self.assertEqual(armed["armed"], [])
            listed = json.loads(invoke_automa(
                "plugins", "list", "--host-name", "chase-sim-chaser", "--step", "memory", "--long", "--json",
                runtime_root=runtime_root,
            ).stdout)["catalog"]
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

            def listed(host_run_id: str) -> str:
                result = invoke_automa("plugins", "list", "--host-name", "chase-sim-chaser",
                                       "--step", "memory", "--long", runtime_root=runtime_root)
                self.assertIn(f"(host run {host_run_id})", result.stdout)
                return result.stdout

            with chase_runtime(runtime_root) as first:
                uploaded = invoke_automa("plugins", "upload", "--host-name", "chase-sim-chaser",
                                         "--file", str(file), "--name", "memory/prototype",
                                         "--entrypoint", "prototype:Prototype", runtime_root=runtime_root)
                self.assertIn(f"(host run {first.loop.run_id})", uploaded.stdout)
                self.assertIn("memory/prototype", listed(first.loop.run_id))
            with chase_runtime(runtime_root) as restarted:
                self.assertNotEqual(restarted.loop.run_id, first.loop.run_id)
                self.assertNotIn("memory/prototype", listed(restarted.loop.run_id))


if __name__ == "__main__":
    unittest.main()
