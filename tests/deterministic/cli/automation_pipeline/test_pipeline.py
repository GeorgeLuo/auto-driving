from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cli.automa_cli.automation import run_host_automation
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.step_activations import stage_host_step
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    HOST_NAME,
    _SlowMapper,
    _write_activations,
    chase_runtime,
    staged_runners,
)


class AutomationLivePipelineTests(unittest.TestCase):
    def test_cli_plugin_update_changes_running_mapper_on_next_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / HOST_NAME)
            sync_controller_bundle(bundle, output=None)
            _write_activations(bundle, plugins=["frame"])
            applied_selections: list[tuple[str, ...]] = []
            cli_updates = []

            def wrap_perception(step, mapper):
                if step != "perception":
                    return
                perceive = mapper.perceive

                def perceive_with_cli_updates(request):
                    result = perceive(request)
                    applied_selections.append(tuple(mapper.plugin_ids))
                    if len(applied_selections) == 1:
                        cli_updates.append(stage_host_step(
                            host_name=HOST_NAME, step="perception", plugins=["frame", "floor_plane"],
                            runtime_root=runtime_root, json_output=True,
                        ))
                    elif len(applied_selections) == 2:
                        cli_updates.append(stage_host_step(
                            host_name=HOST_NAME, step="perception", plugins=["floor_plane"],
                            runtime_root=runtime_root, json_output=True,
                        ))
                    return result

                mapper.perceive = perceive_with_cli_updates

            with (
                staged_runners(wrap=wrap_perception),
                chase_runtime(runtime_root),
            ):
                result = run_host_automation(
                    host_name=HOST_NAME, num_decisions=3, take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertEqual(
                applied_selections,
                [("frame",), ("frame", "floor_plane"), ("floor_plane",)],
            )
            self.assertEqual([code for code, _ in cli_updates], [0, 0])
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads((automation_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["steps"]["perception"]["plugin_ids"], ["floor_plane"])
            latest = json.loads((automation_dir / "latest_perception.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["step_activations"]["perception"]["plugins"], ["floor_plane"])
            self.assertIn("memory", latest)

    def test_a_decision_view_failure_is_recorded_and_does_not_stop_automation(self) -> None:
        """A view failure cannot change the completed cycle's authority result."""

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / HOST_NAME)
            _write_activations(bundle)

            with (
                staged_runners(perception=_SlowMapper()),
                chase_runtime(runtime_root),
                patch(
                    "cli.automa_cli.runtime_monitor.DecisionViewAdapter.refresh",
                    side_effect=RuntimeError("decision view unavailable"),
                ),
                patch("cli.automa_cli.decision_view.DecisionView.invalidate_latest", autospec=True) as invalidate,
            ):
                result = run_host_automation(
                    host_name=HOST_NAME, num_decisions=1, take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            invalidate.assert_called()
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads((automation_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual((state["status"], state["processed_count"]), ("completed", 1))
            self.assertEqual(state["decision"]["latest_frame_publish_skips"], 1)
            self.assertEqual(
                state["decision"]["latest_frame_publish_skip_reason"],
                "RuntimeError: decision view unavailable",
            )
            latest = json.loads((automation_dir / "latest_perception.json").read_text(encoding="utf-8"))
            self.assertEqual((latest["control"]["steering"], latest["control"]["throttle"]), (0.0, 0.0))
            self.assertFalse(latest["control"]["applied"])


if __name__ == "__main__":
    unittest.main()
