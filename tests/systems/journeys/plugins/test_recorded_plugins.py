"""Upload -> record -> host teardown -> moved recording -> public replay flows."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import DECISION_STEPS, activation_generation_id
from autonomy.decision_cycle.steps import decision_steps
from autonomy.plugins import uploaded_source
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.shared.cli.workbench_fixtures import get_json, image_source, post_action
from tests.shared.integration.plugin_flow_fixtures import candidate, frame, replay, upload, write_plugin
from tests.support.cli_runner import run_automa


class RecordedPluginFlows(unittest.TestCase):
    def test_both_hosts_replay_uploaded_revisions_after_catalog_cleanup_and_recording_move(self):
        for vehicle in ("chase", "picar"):
            with self.subTest(vehicle=vehicle), image_source(1) as root:
                host = (create_chase_host(ChaseSimCar(), steps=decision_steps()) if vehicle == "chase"
                        else create_picar_host(steps=decision_steps()))
                self.addCleanup(host.close)
                host.follow_activations({}, root / "runtime")
                view = RuntimeViewServer(vehicle_id=vehicle, automation_dir=root / "automation",
                                         port=0, plugin_catalog=host.plugin_catalog).start()
                self.addCleanup(view.stop)
                recording = RunRecording(root / "runs", vehicle_id=vehicle)
                host.start(RunConfiguration(mode="observe_only", num_decisions=3), recording=recording)
                host.run(frame(root, 0))
                originals = set()
                for step in ("perception", "memory", "proposal"):
                    file = root / f"{step}.py"
                    write_plugin(file, step, f"prototype-{step}", 1)
                    _, receipt = upload(view.url, file, step, f"prototype-{step}", arm=True)
                    originals.add(uploaded_source(receipt["plugin"]["metadata"]["source_file"]))
                host.run(frame(root, 1))
                write_plugin(root / "proposal.py", "proposal", "prototype-proposal", 2)
                _, receipt = upload(view.url, root / "proposal.py", "proposal", "prototype-proposal", arm=True)
                originals.add(uploaded_source(receipt["plugin"]["metadata"]["source_file"]))
                host.run(frame(root, 2))
                self.assertEqual(host.session_status()["status"], "completed")
                before = json.loads((recording.root / "manifest.json").read_text())
                if vehicle == "picar":
                    # A retained-source recording from before content-addressed
                    # entrypoints: keep its identity, normalize only at replay.
                    for entry in before["frames"]:
                        references = {}
                        for activation in entry["steps"].values():
                            if activation is None:
                                continue
                            for plugin_id, spec in activation["plugin_specs"].items():
                                module, _, name = spec.partition(":")
                                if module.endswith(".py"):
                                    original = f"/removed-host/uploads/{module}"
                                    activation["plugin_specs"][plugin_id] = f"{original}:{name}"
                                    references[original] = f"plugins/{module}"
                        entry["plugin_sources"] = references
                        entry["generation_id"] = activation_generation_id(
                            {step: entry["steps"][step] for step in DECISION_STEPS}, prefix="decision",
                        )
                    (recording.root / "manifest.json").write_text(json.dumps(before))
                view.stop()
                host.close()
                for directory in {path.parent for path in originals}:
                    shutil.rmtree(directory)
                self.assertTrue(all(not path.exists() for path in originals))
                moved = root / "moved"
                shutil.move(recording.root, moved)
                self.assertEqual(json.loads((moved / "manifest.json").read_text()), before)
                cli = run_automa("vehicles", "workbench", "replay", str(moved), "--cadence-ms", "0")
                self.assertIn("phase: completed", cli.stdout)
                base, run_id = replay(self, moved)
                for position, revision in ((1, 1), (2, 2)):
                    shown = post_action(base, {"action": "seek", "run_id": run_id, "position": position})["state"]
                    restored = candidate(shown, "prototype-proposal")
                    self.assertEqual(restored["metadata"]["revision"], revision)
                    self.assertEqual(restored["metadata"]["memory"]["count"], position)
                # Seeking back must survive state polling and a live availability change.
                earlier = post_action(base, {"action": "seek", "run_id": run_id, "position": 0})["state"]
                self.assertEqual(earlier["active_perception_plugin_ids"], [])
                write_plugin(root / "unselected.py", "perception", "unselected", 3)
                upload(base, root / "unselected.py", "perception", "unselected")
                for _ in range(3):
                    polled = get_json(base, "/api/state")
                    for step in ("perception", "memory", "proposal"):
                        self.assertEqual(polled[f"active_{step}_plugin_ids"], earlier[f"active_{step}_plugin_ids"])
                    self.assertEqual(polled["machine_detail"]["pipeline"], earlier["machine_detail"]["pipeline"])
                    self.assertEqual(polled["steps"], earlier["steps"])
                    self.assertEqual(polled["current_frame"], earlier["current_frame"])
                    catalog = polled["perception_plugin_catalog"]["plugins"]
                    self.assertIn("unselected", [item["id"] for item in catalog])
                    self.assertTrue(all(not item["active"] for item in catalog))


if __name__ == "__main__":
    unittest.main()
