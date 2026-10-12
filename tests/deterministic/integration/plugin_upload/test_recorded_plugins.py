"""Upload -> record -> host teardown -> moved recording -> public replay flows."""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.plugin_loader import load_runner
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.session import RunConfiguration
from implementations.runtime.picar import create_host as create_picar_host
from tests.shared.cli.workbench_fixtures import image_source, post_action
from tests.shared.integration.plugin_flow_fixtures import candidate, frame, replay, write_plugin


class RecordedPluginFlows(unittest.TestCase):

    def test_an_armed_upload_is_recorded_with_the_source_it_ran(self):
        with image_source(1) as root:
            host = create_picar_host(steps=decision_steps())
            self.addCleanup(host.close)
            recording = RunRecording(root / "runs", vehicle_id="picar")
            host.start(RunConfiguration(mode="observe_only", num_decisions=1), recording=recording)
            file = root / "prototype.py"
            write_plugin(file, "proposal", "prototype", 1)
            receipt = host.plugin_catalog({"step": "proposal", "plugin_id": "prototype", "entrypoint": "prototype:Prototype",
                                           "source_base64": base64.b64encode(file.read_bytes()).decode()})
            self.assertTrue(host.plugin_catalog({"operation": "arm", "selections": {"proposal": ["prototype"]}})["ok"])
            result = host.run(frame(root, 0))
            self.assertEqual(result.proposal.candidates[0].metadata["revision"], 1)
            name = receipt["plugin"]["metadata"]["source_file"]
            self.assertEqual((recording.root / "plugins" / name).read_bytes(), file.read_bytes())
            manifest = json.loads((recording.root / "manifest.json").read_text())
            self.assertEqual(manifest["frames"][0]["steps"]["proposal"]["plugin_specs"], {"prototype": f"{name}:Prototype"})

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
