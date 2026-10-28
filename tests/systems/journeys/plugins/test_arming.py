"""Arming through the executable, on an in-process host and on a worker it starts."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import write_host_fixture
from unittest.mock import patch
from urllib.request import urlopen

from cli.automa_cli.runtime_hosts import stop_chase_host
from implementations.decision_cycle.catalog import PRESET_STEPS, packaged_activation
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import (
    catalog,
    frame,
    plugin_host,
    proposal,
    selected,
    upload,
    write_plugin,
)
from tests.support.cli_runner import run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server


class PluginArmingFlows(unittest.TestCase):
    def test_grouped_dependencies_adopt_on_one_frame_in_both_vehicle_hosts(self):
        activation = packaged_activation("perception", ["frame", "multi_obstruction_tracks"],
            config_overrides={"multi_obstruction_tracks": {"floor_cutoff_y": 0.88}})
        for vehicle in ("chase", "picar"):
            with (self.subTest(vehicle=vehicle), image_source(1) as root,
                  plugin_host(root, vehicle, {"perception": activation}) as (host, base)):
                before = catalog(base)
                session = host.session_status()
                for step in PRESET_STEPS:
                    file = root / f"{step}.py"
                    write_plugin(file, step, f"prototype-{step}", 1)
                    upload(base, file, step, f"prototype-{step}")
                # Upload leaves every current definition and the session alone.
                available = catalog(base)
                self.assertEqual(available["arming"], before["arming"])
                self.assertEqual(available["applied_decision"], before["applied_decision"])
                selection = root / "selection.json"
                selection.write_text(json.dumps({step: [f"prototype-{step}"] for step in PRESET_STEPS}))
                # Through the executable once; the worker journey below covers
                # upload, status and disarm the same way.
                receipt = json.loads(run_automa("hosts", "plugins", "arm", "--url", base,
                                                "--selection", str(selection), "--json").stdout)
                self.assertEqual(receipt["status"], "requested")
                self.assertEqual(receipt["outcome"]["status"], "requested")
                self.assertEqual(host.session_status(), session)
                pending = catalog(base)
                self.assertEqual(selected(pending, "applied", "perception"), selected(before, "applied", "perception"))
                self.assertEqual(selected(pending, "requested", "perception")[:-1], selected(before, "applied", "perception"))
                result = host.run(frame(root, 1))
                applied = catalog(base)
                self.assertEqual(applied["arming"]["status"], "applied")
                self.assertEqual(applied["arming"]["requested"], applied["arming"]["applied"])
                self.assertEqual(applied["applied_decision"]["steps"]["proposal"]["plugins"], ["prototype-proposal"])
                self.assertNotEqual(applied["applied_decision"]["generation_id"], before["applied_decision"]["generation_id"])
                self.assertEqual(proposal(result, "prototype-proposal").metadata["memory"],
                                 {"revision": 1, "perception": 1, "count": 1})
                self.assertEqual(host.session_status()["status"], "pre_start")
                human = invoke_automa("hosts", "plugins", "status", "--url", base).stdout
                self.assertIn("Arming: applied", human)
                self.assertIn("Applied proposal: prototype-proposal", human)

    def test_cli_worker_view_and_stream_adopt_new_proposals_without_restarting(self):
        with tempfile.TemporaryDirectory() as directory, fake_metrics_ui_server() as ws_url:
            root = Path(directory)
            runtime = root / "vehicles"
            env = {"CHASE_UI_WS_URL": ws_url, "BROWSER": "true"}
            write_host_fixture(runtime, "chase-sim-chaser", url=ws_url)

            def cli(*args, check=True):
                return run_automa("hosts", *args, runtime_root=runtime, extra_env=env, check=check)

            cli("stage", "perception", "--name", "chase-sim-chaser", "--preset", "lightweight_observer")
            try:
                cli("run", "--name", "chase-sim-chaser", "--observe-only", "--num-decisions", "0", "--record")
                original = json.loads(cli("status", "--name", "chase-sim-chaser", "--json").stdout)["hosts"][0]["run"]
                before = json.loads(cli("plugins", "status", "--name", "chase-sim-chaser", "--json").stdout)
                file = root / "proposal.py"
                for revision in (1, 2):
                    write_plugin(file, "proposal", "prototype", revision)
                    receipt = json.loads(cli("plugins", "upload", "--name", "chase-sim-chaser", "--file", str(file),
                                             "--step", "proposal", "--plugin-id", "prototype", "--entrypoint", "prototype:Prototype",
                                             "--arm", "--json").stdout)
                    self.assertTrue(receipt["ok"])
                    deadline = time.monotonic() + 5
                    while True:
                        shown = json.loads(cli("plugins", "status", "--name", "chase-sim-chaser", "--json").stdout)
                        if shown["arming"]["status"] == "applied":
                            break
                        self.assertLess(time.monotonic(), deadline, shown)
                        time.sleep(0.05)
                    self.assertEqual(selected(shown, "applied", "proposal")[0], receipt["plugin"])
                    streamed = json.loads(cli("stream", "proposal", "--name", "chase-sim-chaser", "--once", "--json").stdout)
                    self.assertEqual(streamed["plugins"], ["prototype"])
                    self.assertEqual(streamed["generation_id"], shown["applied_decision"]["generation_id"])
                    self.assertEqual(streamed["record"]["candidates"][0]["metadata"]["revision"], revision)
                    view_url = original["published_view"]["url"].rstrip("/")
                    # The run's monitor publishes the host's decision to the view on its next poll.
                    deadline = time.monotonic() + 5
                    while True:
                        with urlopen(view_url + "/api/health", timeout=3) as response:
                            health = json.load(response)["decision"]
                        if (health["identity"] or {}).get("producer_generation_id") == streamed["generation_id"]:
                            break
                        self.assertLess(time.monotonic(), deadline, health)
                        time.sleep(0.05)
                    self.assertEqual(health["status"], "running")
                    with urlopen(view_url + "/api/decision/latest?generation=" + health["generation_id"], timeout=3) as response:
                        viewed = json.load(response)
                    self.assertEqual(viewed["decision"]["generation_id"], streamed["generation_id"])
                    self.assertEqual(viewed["decision"]["cycle"]["proposal"]["candidates"][0]["metadata"]["revision"], revision)
                    current = json.loads(cli("status", "--name", "chase-sim-chaser", "--json").stdout)["hosts"][0]["run"]
                    self.assertEqual(current["state"]["run_id"], original["state"]["run_id"])
                    self.assertEqual(current["state"]["pid"], original["state"]["pid"])
                    self.assertEqual(current["state"]["action_policy"], "observe_only")
                    self.assertEqual(current["decision"]["generation_id"], streamed["generation_id"])
                    self.assertNotEqual(streamed["generation_id"], before["applied_decision"]["generation_id"])
                disarmed = json.loads(cli("plugins", "disarm", "--name", "chase-sim-chaser",
                                          "--step", "proposal", "--plugin", "prototype", "--json").stdout)
                self.assertTrue(disarmed["ok"])
                deadline = time.monotonic() + 5
                while True:
                    shown = json.loads(cli("plugins", "status", "--name", "chase-sim-chaser", "--json").stdout)
                    if shown["arming"]["status"] == "applied":
                        break
                    self.assertLess(time.monotonic(), deadline, shown)
                    time.sleep(0.05)
                self.assertEqual(selected(shown, "applied", "proposal"), [])
                self.assertIn(receipt["plugin"], shown["plugins"])
                streamed = json.loads(cli("stream", "proposal", "--name", "chase-sim-chaser", "--once", "--json").stdout)
                self.assertEqual(streamed["plugins"], [])
                self.assertEqual(streamed["generation_id"], shown["applied_decision"]["generation_id"])
                deadline = time.monotonic() + 5
                while True:
                    with urlopen(view_url + "/api/health", timeout=3) as response:
                        health = json.load(response)["decision"]
                    if (health["identity"] or {}).get("producer_generation_id") == streamed["generation_id"]:
                        break
                    self.assertLess(time.monotonic(), deadline, health)
                    time.sleep(0.05)
                with urlopen(view_url + "/api/decision/latest?generation=" + health["generation_id"], timeout=3) as response:
                    viewed = json.load(response)
                self.assertEqual(viewed["decision"]["cycle"]["proposal"]["candidates"], [])
                current = json.loads(cli("status", "--name", "chase-sim-chaser", "--json").stdout)["hosts"][0]["run"]
                self.assertEqual(current["state"]["run_id"], original["state"]["run_id"])
                self.assertEqual(current["state"]["pid"], original["state"]["pid"])
                self.assertEqual(current["state"]["action_policy"], "observe_only")
                # The 30 Hz stream records throughout all of the CLI calls.
                # Allow its final export to drain before forced termination.
                cli("stop", "--name", "chase-sim-chaser", "--wait-s", "10")
                automation_dir = runtime / "chase-sim-chaser" / "bundle/runtime/automation"
                state = json.loads((automation_dir / "state.json").read_text())
                self.assertEqual(state["status"], "stopped", state.get("error"))
                self.assertNotEqual(state["stop_reason"], "stop_command_forced")
                self.assertGreater(state["recorded_count"], 0)
                self.assertEqual(state["recorded_count"], state["processed_count"])
                # The CLI exports beside the host's history, never into it.
                self.assertEqual(Path(state["run_dir"]).resolve().parent, (automation_dir / "runs").resolve())
                exported = json.loads((Path(state["run_dir"]) / "manifest.json").read_text())
                host_manifest = automation_dir / "host-runs" / Path(state["run_dir"]).name / "manifest.json"
                self.assertEqual(exported, json.loads(host_manifest.read_text()))
                self.assertEqual(len(exported["frames"]), state["recorded_count"])
                # The host outlives the run and keeps live deselections, like a PiCar's.
                after = json.loads(cli("plugins", "status", "--name", "chase-sim-chaser", "--json").stdout)
                self.assertEqual(selected(after, "applied", "proposal"), [])
            finally:
                cli("stop", "--name", "chase-sim-chaser", check=False)
                with patch("cli.automa_cli.runtime_hosts.RUNTIME_ROOT", runtime):
                    stop_chase_host("chase-sim-chaser")


if __name__ == "__main__":
    unittest.main()
