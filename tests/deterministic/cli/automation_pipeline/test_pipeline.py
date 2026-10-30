from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cli.automa_cli.hosts.runs.automation import run_host_automation
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.shared.controller_releases import controller_bundle_paths
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    HOST_NAME,
    _SlowMapper,
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
            sync_controller_bundle(bundle, output=None)
            _write_activations(bundle, plugins=["frame"])
            applied_selections: list[tuple[str, ...]] = []
            frame_selections: dict[str, list[str]] = {}

            def wrap_perception(step, mapper):
                if step != "perception":
                    return
                perceive = mapper.perceive

                def perceive_then_restage(request):
                    result = perceive(request)
                    applied_selections.append(tuple(mapper.plugin_ids))
                    frame_selections[request.sensor_frame.read_id] = list(mapper.plugin_ids)
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
                result = run_host_automation(
                    host_name=HOST_NAME, num_decisions=3, take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertEqual(
                applied_selections,
                [("frame",), ("frame", "floor_plane"), ("floor_plane",)],
            )
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads((automation_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["steps"]["perception"]["plugin_ids"], ["floor_plane"])
            latest = json.loads((automation_dir / "latest_perception.json").read_text(encoding="utf-8"))
            # The latest copy can be any decided frame, so it is checked against its own frame.
            self.assertEqual(
                latest["step_activations"]["perception"]["plugins"], frame_selections[latest["frame_id"]],
            )
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
                    "cli.automa_cli.hosts.runs.runtime_monitor.DecisionViewAdapter.refresh",
                    side_effect=RuntimeError("decision view unavailable"),
                ),
                patch("cli.automa_cli.frontend.decision_view.DecisionView.invalidate_latest", autospec=True) as invalidate,
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
