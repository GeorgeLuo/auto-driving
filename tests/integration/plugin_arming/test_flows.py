"""CLI -> catalog -> frame adoption -> publication, on the shared vehicle host."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli.runtime_hosts import stop_chase_host
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.cli.workbench_fixtures import ImageReplayRunner, image_source, serve_workbench
from tests.support.cli_runner import run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server


def write_plugin(file: Path, step: str, plugin_id: str, revision: int) -> None:
    """Self-contained plugins with runtime dependencies across the three steps."""
    if step == "perception":
        source = (
            "from dataclasses import replace\n"
            "from implementations.decision_cycle.perception.plugins.frame.plugin import FrameObservationPlugin\n"
            "class Prototype(FrameObservationPlugin):\n"
            f"    plugin_id = {plugin_id!r}\n"
            "    def perceive(self, inputs):\n"
            f"        inputs.shared_memory['prototype.perception'] = {revision}\n"
            "        result = super().perceive(inputs)\n"
            f"        return replace(result, measurements={{**result.measurements, 'revision': {revision}}})\n"
        )
    elif step == "memory":
        source = (
            "class Prototype:\n"
            f"    plugin_id = {plugin_id!r}\n"
            "    def update(self, context, observation):\n"
            "        memory = context.shared_memory\n"
            "        prior = memory.get('prototype.memory', {})\n"
            f"        memory['prototype.memory'] = {{'revision': {revision}, 'perception': memory['prototype.perception'], 'count': prior.get('count', 0) + 1}}\n"
            "    def reset(self, shared_memory):\n"
            "        shared_memory.pop('prototype.memory', None)\n"
            "    def status(self, shared_memory):\n"
            "        return dict((shared_memory or {}).get('prototype.memory', {}))\n"
        )
    else:
        source = (
            "from autonomy.decision_cycle.proposal.values import ActionProposal\n"
            "class Prototype:\n"
            f"    plugin_id = {plugin_id!r}\n"
            "    def propose(self, source, shared_memory):\n"
            "        memory = dict(shared_memory.get('prototype.memory', {}))\n"
            "        return ActionProposal(plugin_id=self.plugin_id, frame_id=source.frame_id,\n"
            f"            lifecycle='inactive', freshness='none', confidence=0.0, reason='prototype revision {revision}',\n"
            f"            command=None, metadata={{'revision': {revision}, 'memory': memory}})\n"
        )
    file.write_text(source)


def upload(base, file, step, plugin_id, *, arm=False, check=True):
    command = ["vehicles", "plugins", "upload", "--url", base, "--file", str(file),
               "--step", step, "--plugin-id", plugin_id, "--entrypoint", "prototype:Prototype", "--json"]
    if arm:
        command.append("--arm")
    result = run_automa(*command, check=check)
    return result, json.loads(result.stdout)


def catalog(base):
    return json.loads(run_automa("vehicles", "plugins", "status", "--url", base, "--json").stdout)


def arm_step(base, step, *ids):
    command = ["vehicles", "plugins", "arm", "--url", base, "--step", step, "--json"]
    for plugin_id in ids:
        command.extend(["--plugin", plugin_id])
    return json.loads(run_automa(*command).stdout)


def selected(state, phase, step):
    return state["arming"][phase][step]


def proposal(result, plugin_id):
    return next(item for item in result.proposal.candidates if item.plugin_id == plugin_id)


def frame(root, index):
    now = int(time.time() * 1000)
    reading = SensorReading(sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
                            captured_at_ms=now, path=str(next(root.glob("*.png"))),
                            metadata={"content_type": "image/png"})
    sensor = SensorFrame(read_id=f"frame-{index}", readings={FRONT_CAMERA_SENSOR_ID: reading},
                         started_at_ms=now, completed_at_ms=now)
    return DecisionFrameContext(f"frame-{index}", index, now, sensor_frame=sensor)


class PluginArmingFlows(unittest.TestCase):
    @contextmanager
    def host(self, root, vehicle="chase", activations=None):
        steps = decision_steps(activations)
        host = (create_chase_host(ChaseSimCar(), steps=steps) if vehicle == "chase"
                else create_picar_host(steps=steps))
        host.follow_activations(activations or {}, root / "runtime")
        view = RuntimeViewServer(vehicle_id=vehicle, automation_dir=root / "automation",
                                 port=0, plugin_catalog=host.plugin_catalog).start()
        try:
            yield host, view.url
        finally:
            view.stop()
            host.close()

    def test_grouped_dependencies_adopt_on_one_frame_in_both_vehicle_hosts(self):
        activation = packaged_activation("perception", ["frame", "multi_obstruction_tracks"],
            config_overrides={"multi_obstruction_tracks": {"floor_cutoff_y": 0.88}})
        for vehicle in ("chase", "picar"):
            with (self.subTest(vehicle=vehicle), image_source(1) as root,
                  self.host(root, vehicle, {"perception": activation}) as (host, base)):
                before = catalog(base)
                session = host.session_status()
                for step in ("perception", "memory", "proposal"):
                    file = root / f"{step}.py"
                    write_plugin(file, step, f"prototype-{step}", 1)
                    upload(base, file, step, f"prototype-{step}")
                # Upload leaves every current definition and the session alone.
                available = catalog(base)
                self.assertEqual(available["arming"], before["arming"])
                self.assertEqual(available["applied_decision"], before["applied_decision"])
                selection = root / "selection.json"
                selection.write_text(json.dumps({step: [f"prototype-{step}"] for step in
                                                ("perception", "memory", "proposal")}))
                receipt = json.loads(run_automa("vehicles", "plugins", "arm", "--url", base,
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
                self.assertEqual(host.session_status()["status"], "stopped")
                human = run_automa("vehicles", "plugins", "status", "--url", base).stdout
                self.assertIn("Arming: applied", human)
                self.assertIn("Applied proposal: prototype-proposal", human)

    def test_available_requested_and_applied_revisions_stay_independent(self):
        with image_source(1) as root, self.host(root) as (host, base):
            file = root / "proposal.py"
            write_plugin(file, "proposal", "prototype", 1)
            _, first = upload(base, file, "proposal", "prototype", arm=True)
            self.assertEqual(first["upload"]["status"], "uploaded")
            self.assertEqual(first["status"], "requested")
            self.assertEqual(proposal(host.run(frame(root, 1)), "prototype").metadata["revision"], 1)
            old_identity = catalog(base)["applied_decision"]
            write_plugin(file, "proposal", "prototype", 2)
            _, second = upload(base, file, "proposal", "prototype")
            available = catalog(base)
            self.assertEqual(selected(available, "applied", "proposal")[0], first["plugin"])
            self.assertEqual(available["applied_decision"], old_identity)
            self.assertEqual(proposal(host.run(frame(root, 2)), "prototype").metadata["revision"], 1)
            arm_step(base, "proposal", "prototype")
            write_plugin(file, "proposal", "prototype", 3)
            _, third = upload(base, file, "proposal", "prototype")
            queued = catalog(base)
            self.assertEqual(selected(queued, "requested", "proposal")[0], second["plugin"])
            self.assertEqual(selected(queued, "applied", "proposal")[0], first["plugin"])
            self.assertEqual(next(item for item in queued["plugins"] if item["id"] == "prototype"), third["plugin"])
            self.assertEqual(proposal(host.run(frame(root, 3)), "prototype").metadata["revision"], 2)
            identity2 = catalog(base)["applied_decision"]
            self.assertNotEqual(identity2["generation_id"], old_identity["generation_id"])
            arm_step(base, "proposal", "prototype")
            self.assertEqual(proposal(host.run(frame(root, 4)), "prototype").metadata["revision"], 3)
            self.assertNotEqual(catalog(base)["applied_decision"]["generation_id"], identity2["generation_id"])

    def test_rapid_requests_append_in_order_and_rearming_keeps_memory_history(self):
        with image_source(1) as root, self.host(root) as (host, base):
            for step, plugin_id in (("perception", "prototype-perception"), ("memory", "prototype-memory"),
                                    ("proposal", "first"), ("proposal", "second"), ("proposal", "third")):
                file = root / f"{plugin_id}.py"
                write_plugin(file, step, plugin_id, 1)
                upload(base, file, step, plugin_id)
                arm_step(base, step, plugin_id)
            queued = catalog(base)
            self.assertEqual([item["id"] for item in selected(queued, "requested", "proposal")],
                             ["first", "second", "third"])
            first = host.run(frame(root, 1))
            self.assertEqual(proposal(first, "first").metadata["memory"]["count"], 1)
            identity = catalog(base)["applied_decision"]
            for step, plugin_id in (("memory", "prototype-memory"), ("proposal", "first")):
                receipt = arm_step(base, step, plugin_id)
                self.assertEqual(receipt["status"], "applied")
            second = host.run(frame(root, 2))
            self.assertEqual(proposal(second, "first").metadata["memory"]["count"], 2)
            self.assertEqual(catalog(base)["applied_decision"], identity)

    def test_arming_before_during_and_after_completion_does_not_start_or_change_mode(self):
        with image_source(1) as root, self.host(root) as (host, base):
            file = root / "proposal.py"
            for index, phase in enumerate(("stopped", "running", "completed"), start=1):
                if phase == "running":
                    host.start(RunConfiguration(mode="observe_only", num_decisions=1))
                write_plugin(file, "proposal", "prototype", index)
                before = host.session_status()
                _, receipt = upload(base, file, "proposal", "prototype", arm=True)
                self.assertEqual(receipt["status"], "requested")
                self.assertEqual(host.session_status(), before)
                self.assertEqual(host.session_status()["status"], phase)
                if phase == "completed":
                    host.start(RunConfiguration(mode="observe_only", num_decisions=1))
                host.run(frame(root, index))
                self.assertEqual(catalog(base)["arming"]["status"], "applied")
                if phase != "stopped":
                    self.assertEqual(host.session_status()["status"], "completed")

    def test_an_import_failure_fails_the_arm_and_keeps_the_previous_selection(self):
        with (image_source(1) as root, self.host(root, activations={
                "perception": packaged_activation("perception", ["frame"]),
        }) as (host, base)):
            file = root / "proposal.py"
            write_plugin(file, "proposal", "prototype-proposal", 1)
            file.write_text("import not_uploaded_yet\n" + file.read_text())
            upload(base, file, "proposal", "prototype-proposal")
            before = catalog(base)
            for command in (
                ("arm", "--step", "proposal", "--plugin", "prototype-proposal"),
                ("upload", "--file", str(file), "--step", "proposal", "--plugin-id", "prototype-proposal",
                 "--entrypoint", "prototype:Prototype", "--arm"),
            ):
                with self.subTest(command=command[0]):
                    refused = run_automa("vehicles", "plugins", *command, "--url", base, check=False)
                    self.assertEqual(refused.returncode, 2, refused.stdout)
                    self.assertIn("Arming: failed", refused.stdout)
                    self.assertIn("ModuleNotFoundError: No module named 'not_uploaded_yet'", refused.stdout)
            failed = run_automa("vehicles", "plugins", "status", "--url", base, "--json", check=False)
            self.assertEqual(failed.returncode, 2)
            shown = json.loads(failed.stdout)
            self.assertEqual(shown["arming"]["status"], "failed")
            self.assertEqual(shown["arming"]["requested"], before["arming"]["requested"])
            self.assertEqual(shown["arming"]["applied"], before["arming"]["applied"])
            self.assertEqual(shown["applied_decision"], before["applied_decision"])
            host.run(frame(root, 1))
            write_plugin(file, "proposal", "prototype-proposal", 2)
            upload(base, file, "proposal", "prototype-proposal", arm=True)
            self.assertEqual(proposal(host.run(frame(root, 2)), "prototype-proposal").metadata["revision"], 2)
            self.assertEqual(catalog(base)["arming"]["status"], "applied")

    def test_upload_remains_available_when_composed_arming_fails_on_a_workbench(self):
        root = self.enterContext(image_source(1))
        base = serve_workbench(self, ImageReplayRunner(root))
        file = root / "prototype.py"
        write_plugin(file, "perception", "prototype", 1)
        result, receipt = upload(base, file, "perception", "prototype", arm=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["upload"]["status"], "uploaded")
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("runtime host", receipt["error"])
        self.assertIn(receipt["plugin"], catalog(base)["plugins"])

    def test_cli_worker_view_and_stream_adopt_new_proposals_without_restarting(self):
        with tempfile.TemporaryDirectory() as directory, fake_metrics_ui_server() as ws_url:
            root = Path(directory)
            runtime = root / "vehicles"
            env = {"CHASE_UI_WS_URL": ws_url, "BROWSER": "true"}

            def cli(*args, check=True):
                return run_automa("vehicles", *args, runtime_root=runtime, extra_env=env, check=check)

            cli("update", "perception", "--id", "chase-sim-chaser", "--preset", "lightweight_observer")
            try:
                cli("automation", "run", "--id", "chase-sim-chaser", "--observe-only", "--num-decisions", "0")
                original = json.loads(cli("automation", "status", "--id", "chase-sim-chaser", "--json").stdout)["vehicles"][0]
                before = json.loads(cli("plugins", "status", "--id", "chase-sim-chaser", "--json").stdout)
                file = root / "proposal.py"
                for revision in (1, 2):
                    write_plugin(file, "proposal", "prototype", revision)
                    receipt = json.loads(cli("plugins", "upload", "--id", "chase-sim-chaser", "--file", str(file),
                                             "--step", "proposal", "--plugin-id", "prototype", "--entrypoint", "prototype:Prototype",
                                             "--arm", "--json").stdout)
                    self.assertTrue(receipt["ok"])
                    deadline = time.monotonic() + 5
                    while True:
                        shown = json.loads(cli("plugins", "status", "--id", "chase-sim-chaser", "--json").stdout)
                        if shown["arming"]["status"] == "applied":
                            break
                        self.assertLess(time.monotonic(), deadline, shown)
                        time.sleep(0.05)
                    self.assertEqual(selected(shown, "applied", "proposal")[0], receipt["plugin"])
                    streamed = json.loads(cli("stream", "proposal", "--id", "chase-sim-chaser", "--once", "--json").stdout)
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
                    current = json.loads(cli("automation", "status", "--id", "chase-sim-chaser", "--json").stdout)["vehicles"][0]
                    self.assertEqual(current["state"]["run_id"], original["state"]["run_id"])
                    self.assertEqual(current["state"]["pid"], original["state"]["pid"])
                    self.assertEqual(current["state"]["action_policy"], "observe_only")
                    self.assertEqual(current["decision"]["generation_id"], streamed["generation_id"])
                    self.assertNotEqual(streamed["generation_id"], before["applied_decision"]["generation_id"])
                cli("automation", "stop", "--id", "chase-sim-chaser")
                # The host outlives the run and keeps the armed catalog, like a PiCar's.
                after = json.loads(cli("plugins", "status", "--id", "chase-sim-chaser", "--json").stdout)
                self.assertEqual(selected(after, "applied", "proposal")[0], receipt["plugin"])
            finally:
                cli("automation", "stop", "--id", "chase-sim-chaser", check=False)
                with patch("cli.automa_cli.runtime_hosts.RUNTIME_ROOT", runtime):
                    stop_chase_host("chase-sim-chaser")


if __name__ == "__main__":
    unittest.main()
