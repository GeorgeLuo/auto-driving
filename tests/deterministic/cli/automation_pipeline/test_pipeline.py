from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from cli.automa_cli.hosts.runs.service import run_host_automation
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.releases.paths import controller_bundle_paths
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    HOST_NAME,
    _write_activations,
    chase_runtime,
    staged_runners,
)
from tests.support.runtime_fixtures import stage_step


class AutomationLivePipelineTests(unittest.TestCase):
    def test_a_restaged_selection_changes_the_running_mapper_on_next_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / HOST_NAME)
            sync_controller_bundle(bundle, emit=None)
            _write_activations(bundle, plugins=["frame"])
            applied_selections: list[tuple[str, ...]] = []

            def wrap_perception(step, mapper):
                if step != "perception":
                    return
                perceive = mapper.perceive

                def perceive_then_restage(request):
                    result = perceive(request)
                    applied_selections.append(tuple(mapper.plugin_ids))
                    if len(applied_selections) == 1:
                        stage_step(runtime_root, HOST_NAME, "perception", plugins=["frame", "floor_plane"])
                    elif len(applied_selections) == 2:
                        stage_step(runtime_root, HOST_NAME, "perception", plugins=["floor_plane"])
                    return result

                mapper.perceive = perceive_then_restage

            with (
                staged_runners(wrap=wrap_perception),
                chase_runtime(runtime_root),
            ):
                result = run_host_automation(host_name=HOST_NAME, num_decisions=3, take_control=False)

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertEqual(
                applied_selections,
                [("frame",), ("frame", "floor_plane"), ("floor_plane",)],
            )
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads((automation_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["steps"]["perception"]["plugin_ids"], ["floor_plane"])
            # The latest frame is the host's own metadata; the run writes no mirror of it.
            self.assertTrue(state["last_frame"]["frame_id"])
            self.assertFalse((automation_dir / "latest_perception.json").exists())


if __name__ == "__main__":
    unittest.main()
