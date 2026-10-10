"""Upload -> record -> host teardown -> moved recording -> public replay flows."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import DECISION_STEPS, activation_generation_id, step_activation
from autonomy.decision_cycle.steps import decision_steps
from autonomy.plugins import uploaded_source
from autonomy.runtime.plugin_loader import load_runner
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.cli.workbench_fixtures import ImageReplayRunner, _wait_until, image_source, post_action, serve_workbench
from tests.integration.plugin_arming.test_flows import frame, upload, write_plugin
from tests.integration.plugin_upload.test_flows import get_json
from tests.support.cli_runner import run_automa


def replay(test, recording: Path):
    runner = ImageReplayRunner(recording, cadence_ms=30000, loop=True)
    base = serve_workbench(test, runner)
    response = post_action(base, {"action": "start"})["state"]
    _wait_until(lambda: get_json(base, "/api/state")["current_frame"] is not None)
    post_action(base, {"action": "seek", "run_id": response["run_id"], "position": 0})
    return base, response["run_id"]


def candidate(state, plugin_id):
    return next(item for item in state["steps"]["decision"]["proposal"]["candidates"] if item["plugin_id"] == plugin_id)


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

    def test_bundle_initialization_and_same_spec_source_transition_use_recorded_code(self):
        with image_source(1) as root:
            host = create_picar_host(steps=decision_steps())
            self.addCleanup(host.close)
            recording = RunRecording(root / "runs", vehicle_id="picar")
            host.start(RunConfiguration(mode="observe_only", num_decisions=3), recording=recording)
            for index, module in enumerate(("implementations.bundle_only", "implementations.bundle_only", "implementations.decision_cycle.proposal.plugins.avoid_recent_obstruction.plugin")):
                bundle = root / f"bundle-{index}"
                source = bundle / (module.replace(".", "/") + ".py")
                source.parent.mkdir(parents=True)
                for directory in (bundle / "implementations", *source.parent.parents):
                    if directory.is_relative_to(bundle) and directory != bundle:
                        (directory / "__init__.py").touch()
                source.write_text(
                    "from autonomy.decision_cycle.proposal.values import ActionProposal\n"
                    "class AvoidRecentObstruction:\n"
                    "    plugin_id = 'recorded'\n"
                    "    def propose(self, source, shared_memory):\n"
                    "        return ActionProposal(plugin_id=self.plugin_id, frame_id=source.frame_id,\n"
                    f"            lifecycle='inactive', freshness='none', confidence=0.0, reason='bundle {index}',\n"
                    f"            command=None, metadata={{'bundle': {index}}})\n"
                )
                activation = step_activation("proposal", ["recorded"], {"recorded": f"{module}:AvoidRecentObstruction"},
                                             metadata={"controller_bundle": {"root_dir": str(bundle)}})
                host.set_step("proposal", load_runner(activation))
                result = host.run(frame(root, index))
                self.assertEqual(result.proposal.candidates[0].metadata["bundle"], index)
            host.close()
            base, run_id = replay(self, recording.root)
            for index in range(3):
                state = post_action(base, {"action": "seek", "run_id": run_id, "position": index})["state"]
                self.assertEqual(candidate(state, "recorded")["metadata"]["bundle"], index)


if __name__ == "__main__":
    unittest.main()
