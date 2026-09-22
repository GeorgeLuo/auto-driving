from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cli.automa_cli.automation import run_vehicle_automation
from cli.automa_cli.bundles import controller_bundle_paths
from tests.integration.automation_pipeline.pipeline_fixtures import (
    _FakeCar,
    _SlowMapper,
    _write_activations,
)


class AutomationLivePipelineTests(unittest.TestCase):
    def test_capture_does_not_wait_for_slow_perception_and_latest_frame_wins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            bundle = controller_bundle_paths(runtime_root / vehicle_id)
            _write_activations(bundle)
            mapper = _SlowMapper()
            vehicle = {
                "id": vehicle_id,
                "provider": "chase-sim",
                "connection": {"ws_url": "ws://unused"},
                "status": {
                    "passive_capture": {
                        "status": "available",
                        "session_preservation": {
                            "preserved": True,
                            "unknown_fields": [],
                            "changed_fields": [],
                        },
                    }
                },
            }

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch(
                    "cli.automa_cli.automation.discover_active_vehicles",
                    return_value={},
                ),
                patch(
                    "cli.automa_cli.automation.find_vehicle_by_id",
                    return_value=(vehicle, None),
                ),
                patch("cli.automa_cli.automation.ChaseSimCar", _FakeCar),
                patch("cli.automa_cli.automation._load_mapper", return_value=mapper),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id,
                    interval_s=0.005,
                    frames=8,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["frames_captured"], 8)
            self.assertEqual(state["control_source"], "builtin")
            self.assertEqual(state["action_policy"], "observe_only")
            self.assertEqual(state["control_application"], "not_applied")
            self.assertLess(state["frames_processed"], state["frames_captured"])
            self.assertEqual(
                state["frames_processed"] + state["frames_dropped"],
                state["frames_captured"],
            )
            self.assertEqual(mapper.frame_ids[-1], "chase_frame_000107")
            self.assertTrue(
                (
                    automation_dir / "latest" / "frames" / "latest_front_camera.png"
                ).is_file()
            )
            latest = json.loads(
                (automation_dir / "latest_perception.json").read_text(encoding="utf-8")
            )
            self.assertEqual(latest["simulator_frame_index"], 107)
            self.assertEqual(latest["simulation_epoch"], "chase-run:test")
            self.assertEqual(latest["frame_id"], "chase_frame_000107")
            self.assertEqual(
                latest["shadow_reference"]["simulator_frame_index"],
                latest["simulator_frame_index"],
            )
            self.assertIs(latest["control"]["applied"], False)
            self.assertNotIn("shadow_reference", latest.get("observation") or {})
            self.assertEqual(
                list(
                    (automation_dir / "latest" / "frames").glob(
                        "frame_*_front_camera.png"
                    )
                ),
                [],
            )

    def test_decision_view_publication_failure_does_not_stop_automation(self) -> None:
        """A view failure cannot change the completed cycle's authority result."""

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            bundle = controller_bundle_paths(runtime_root / vehicle_id)
            _write_activations(bundle)
            mapper = _SlowMapper()
            vehicle = {
                "id": vehicle_id,
                "provider": "chase-sim",
                "connection": {"ws_url": "ws://unused"},
                "status": {
                    "passive_capture": {
                        "status": "available",
                        "session_preservation": {
                            "preserved": True,
                            "changed_fields": [],
                            "unknown_fields": [],
                        },
                    }
                },
            }

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch(
                    "cli.automa_cli.automation.discover_active_vehicles",
                    return_value={},
                ),
                patch(
                    "cli.automa_cli.automation.find_vehicle_by_id",
                    return_value=(vehicle, None),
                ),
                patch("cli.automa_cli.automation.ChaseSimCar", _FakeCar),
                patch("cli.automa_cli.automation._load_mapper", return_value=mapper),
                patch(
                    "cli.automa_cli.automation.publish_shadow_decision_frame",
                    return_value=True,
                ),
                patch(
                    "cli.automa_cli.automation._read_latest_decision_frame_for_view",
                    return_value={"frame_id": "view-publication-frame"},
                ),
                patch(
                    "cli.automa_cli.decision_view.DecisionView.publish",
                    side_effect=RuntimeError("decision view unavailable"),
                ),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id,
                    interval_s=0.0,
                    frames=1,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["frames_captured"], 1)
            self.assertEqual(state["frames_processed"], 1)
            latest = json.loads(
                (automation_dir / "latest_perception.json").read_text(encoding="utf-8")
            )
            self.assertEqual(latest["control"]["steering"], 0.0)
            self.assertEqual(latest["control"]["throttle"], 0.0)
            self.assertFalse(latest["control"]["applied"])

    def test_missing_latest_decision_invalidates_view_and_records_skip(self) -> None:
        """A reader bypass cannot leave a cached decision current."""

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            bundle = controller_bundle_paths(runtime_root / vehicle_id)
            _write_activations(bundle)
            mapper = _SlowMapper()
            vehicle = {
                "id": vehicle_id,
                "provider": "chase-sim",
                "connection": {"ws_url": "ws://unused"},
                "status": {
                    "passive_capture": {
                        "status": "available",
                        "session_preservation": {
                            "preserved": True,
                            "changed_fields": [],
                            "unknown_fields": [],
                        },
                    }
                },
            }

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch(
                    "cli.automa_cli.automation.discover_active_vehicles",
                    return_value={},
                ),
                patch(
                    "cli.automa_cli.automation.find_vehicle_by_id",
                    return_value=(vehicle, None),
                ),
                patch("cli.automa_cli.automation.ChaseSimCar", _FakeCar),
                patch("cli.automa_cli.automation._load_mapper", return_value=mapper),
                patch(
                    "cli.automa_cli.automation.publish_shadow_decision_frame",
                    return_value=True,
                ),
                patch(
                    "cli.automa_cli.automation._read_latest_decision_frame_for_view",
                    return_value=None,
                ),
                patch(
                    "cli.automa_cli.decision_view.DecisionView.invalidate_latest",
                    autospec=True,
                ) as invalidate_latest,
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id,
                    interval_s=0.0,
                    frames=1,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            invalidate_latest.assert_called_once()
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["decision"]["latest_frame_publish_skips"], 1)
            self.assertEqual(
                state["decision"]["latest_frame_publish_skip_reason"],
                "decision_view_exact_transaction_unavailable",
            )

    def test_slow_cycle_keeps_its_exact_capture_through_cache_turnover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            bundle = controller_bundle_paths(runtime_root / vehicle_id)
            _write_activations(bundle)
            mapper = _SlowMapper()
            publications: list[tuple[str, tuple[bytes, str] | None]] = []
            vehicle = {
                "id": vehicle_id,
                "provider": "chase-sim",
                "connection": {"ws_url": "ws://unused"},
                "status": {
                    "passive_capture": {
                        "status": "available",
                        "session_preservation": {
                            "preserved": True,
                            "changed_fields": [],
                            "unknown_fields": [],
                        },
                    }
                },
            }

            def accepted_frame(_path, **identity):
                return {"frame_id": identity["frame_id"]}

            def publish_view(*, stream_frame, frame_record, image):
                publications.append((frame_record["frame_id"], image))
                return True

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch(
                    "cli.automa_cli.automation.discover_active_vehicles",
                    return_value={},
                ),
                patch(
                    "cli.automa_cli.automation.find_vehicle_by_id",
                    return_value=(vehicle, None),
                ),
                patch("cli.automa_cli.automation.ChaseSimCar", _FakeCar),
                patch("cli.automa_cli.automation._load_mapper", return_value=mapper),
                patch(
                    "cli.automa_cli.automation.publish_shadow_decision_frame",
                    return_value=True,
                ),
                patch(
                    "cli.automa_cli.automation._read_latest_decision_frame_for_view",
                    side_effect=accepted_frame,
                ),
                patch(
                    "cli.automa_cli.decision_view.DecisionView.publish",
                    side_effect=publish_view,
                ),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id,
                    interval_s=0.0,
                    frames=12,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertTrue(publications)
            first_frame_id, first_image = publications[0]
            self.assertEqual(first_frame_id, mapper.frame_ids[0])
            self.assertEqual(first_frame_id, "chase_frame_000100")
            self.assertIsNotNone(first_image)
            self.assertTrue(first_image[0])
