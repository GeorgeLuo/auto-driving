from __future__ import annotations
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from cli.automa_cli.automation import run_vehicle_automation
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.perception import update_vehicle_perception
from tests.integration.automation_pipeline.pipeline_fixtures import (
    _FakeCar,
    _SlowMapper,
    _write_activations,
    staged_runners,
)


_READY_STATUS = {
    "layers": {
        name: {"state": state}
        for name, state in {
            "simulator_server": "reachable",
            "simulator_frontend": "connected",
            "chase_game": "ready",
            "vehicle": "discoverable",
            "passive_capture": "available",
            "automation_deployment": "deployed",
        }.items()
    },
}


class AutomationLivePipelineTests(unittest.TestCase):
    def test_cli_plugin_update_changes_running_mapper_on_next_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            bundle = controller_bundle_paths(runtime_root / vehicle_id)
            sync_controller_bundle(bundle, output=None)
            _write_activations(bundle, plugins=["frame"])

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
                        cli_updates.append(
                            update_vehicle_perception(
                                vehicle_id=vehicle_id,
                                plugins=["frame", "floor_plane"],
                                json_output=True,
                            )
                        )
                    elif len(applied_selections) == 2:
                        cli_updates.append(
                            update_vehicle_perception(
                                vehicle_id=vehicle_id,
                                plugins=["floor_plane"],
                                json_output=True,
                            )
                        )
                    return result

                mapper.perceive = perceive_with_cli_updates

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.perception.RUNTIME_ROOT", runtime_root),
                patch(
                    "cli.automa_cli.perception.get_vehicle_status",
                    return_value=_READY_STATUS,
                ),
                patch(
                    "cli.automa_cli.automation.discover_active_vehicles",
                    return_value={},
                ),
                patch(
                    "cli.automa_cli.automation.find_vehicle_by_id",
                    return_value=(vehicle, None),
                ),
                patch("cli.automa_cli.automation.create_vehicle_access", side_effect=lambda *_args, **_kw: SimpleNamespace(car=_FakeCar())),
                staged_runners(wrap=wrap_perception),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id,
                    interval_s=0.4,
                    num_decisions=3,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertEqual(
                applied_selections,
                [("frame",), ("frame", "floor_plane"), ("floor_plane",)],
            )
            self.assertEqual([update.exit_code for update in cli_updates], [0, 0])
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["num_decisions"], 3)
            self.assertEqual(state["session"]["processed_decisions"], 3)
            report = state["perception"]["plugin_report"]
            self.assertEqual(report["applied_plugin_ids"], ["floor_plane"])
            self.assertEqual(report["plugins"][0]["plugin_id"], "floor_plane")
            self.assertTrue(report["plugins"][0]["plugin_id"])
            self.assertIsNotNone(report["plugins"][0]["duration_ms"])
            latest = json.loads(
                (automation_dir / "latest_perception.json").read_text(encoding="utf-8")
            )
            self.assertEqual(latest["perception_plugin_report"], report)
            self.assertIn("memory_plugin_report", latest)

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
                patch("cli.automa_cli.automation.create_vehicle_access", side_effect=lambda *_args, **_kw: SimpleNamespace(car=_FakeCar())),
                staged_runners(perception=mapper),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id,
                    interval_s=0.005,
                    num_decisions=8,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["processed_count"], 8)
            self.assertEqual(state["control_source"], "builtin")
            self.assertEqual(state["action_policy"], "observe_only")
            self.assertEqual(state["control_application"], "not_applied")
            self.assertLess(state["processed_count"], state["frames_captured"])
            self.assertEqual(
                state["processed_count"] + state["skipped_count"],
                int(mapper.frame_ids[-1].removeprefix("chase_frame_")) - 100 + 1,
            )
            self.assertGreater(state["skipped_count"], 0)
            self.assertEqual(state["skipped_count"], sum(c.metadata["skipped_since_previous"] for c in mapper.contexts))
            self.assertTrue(
                (
                    automation_dir / "latest" / "frames" / "latest_front_camera.png"
                ).is_file()
            )
            latest = json.loads(
                (automation_dir / "latest_perception.json").read_text(encoding="utf-8")
            )
            self.assertEqual(latest["simulator_frame_index"], mapper.contexts[-1].frame_index)
            self.assertEqual(latest["simulation_epoch"], "chase-run:test")
            self.assertEqual(latest["frame_id"], mapper.frame_ids[-1])
            self.assertEqual(latest["skipped_since_previous"], mapper.contexts[-1].metadata["skipped_since_previous"])
            self.assertEqual(state["last_frame"]["skipped_since_previous"], latest["skipped_since_previous"])
            self.assertEqual(
                latest["chaser_reference"]["simulator_frame_index"],
                latest["simulator_frame_index"],
            )
            self.assertIs(latest["control"]["applied"], False)
            self.assertNotIn("chaser_reference", latest.get("observation") or {})
            self.assertEqual(
                list(
                    (automation_dir / "latest" / "frames").glob(
                        "frame_*_front_camera.png"
                    )
                ),
                [],
            )

    def test_blocked_capture_does_not_delay_decisions_and_tail_is_not_a_skip(self) -> None:
        first_started = threading.Event()
        release_first = threading.Event()
        newest_taken = threading.Event()

        class BlockedCaptureCar(_FakeCar):
            def read_sensors(self, request):
                if self.capture_count == 1 and not first_started.wait(timeout=2.0):
                    raise TimeoutError("decision worker did not start")
                if self.capture_count == 3:
                    # Leave capture 3 in flight while the worker processes
                    # capture 2, replacing capture 1. Capturing must not hold
                    # up the decision on the already-available image.
                    release_first.set()
                    if not newest_taken.wait(timeout=2.0):
                        raise TimeoutError("decision waited for the in-flight capture")
                return super().read_sensors(request)

        class BlockedMapper(_SlowMapper):
            def perceive(self, request):
                if not self.frame_ids:
                    first_started.set()
                    if not release_first.wait(timeout=2.0):
                        raise TimeoutError("test did not release the first decision")
                else:
                    newest_taken.set()
                return super().perceive(request)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            bundle = controller_bundle_paths(runtime_root / vehicle_id)
            _write_activations(bundle)
            mapper = BlockedMapper()
            car = BlockedCaptureCar()
            output = io.StringIO()
            vehicle = {
                "id": vehicle_id, "provider": "chase-sim", "connection": {"ws_url": "ws://unused"},
                "status": {"passive_capture": {"status": "available", "session_preservation": {
                    "preserved": True, "unknown_fields": [], "changed_fields": [],
                }}},
            }
            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation.discover_active_vehicles", return_value={}),
                patch("cli.automa_cli.automation.find_vehicle_by_id", return_value=(vehicle, None)),
                patch("cli.automa_cli.automation.create_vehicle_access", return_value=SimpleNamespace(car=car)),
                staged_runners(perception=mapper),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id, interval_s=0.0, num_decisions=2, take_control=False,
                    verbose=True, output=output,
                )
            self.assertEqual(result.exit_code, 0, result.message)
            state = json.loads((Path(bundle["runtime_dir"]) / "automation" / "state.json").read_text())
            self.assertEqual(mapper.frame_ids, ["chase_frame_000100", "chase_frame_000102"])
            self.assertEqual([c.metadata["skipped_since_previous"] for c in mapper.contexts], [0, 1])
            self.assertEqual(state["skipped_count"], 1)
            self.assertEqual(state["last_frame"]["skipped_since_previous"], 1)
            self.assertGreater(state["frames_captured"], state["processed_count"] + state["skipped_count"])
            self.assertIn("Frames superseded before decision: 1", result.message)
            self.assertIn("skipped_since_previous=1", output.getvalue())

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
                patch("cli.automa_cli.automation.create_vehicle_access", side_effect=lambda *_args, **_kw: SimpleNamespace(car=_FakeCar())),
                staged_runners(perception=mapper),
                patch(
                    "cli.automa_cli.automation.publish_decision_frame",
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
                started_at = time.monotonic()
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id,
                    interval_s=10.0,
                    num_decisions=1,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertLess(time.monotonic() - started_at, 2.0, "completion must wake the capture timer")
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads(
                (automation_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["frames_captured"], 1)
            self.assertEqual(state["processed_count"], 1)
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
                patch("cli.automa_cli.automation.create_vehicle_access", side_effect=lambda *_args, **_kw: SimpleNamespace(car=_FakeCar())),
                staged_runners(perception=mapper),
                patch(
                    "cli.automa_cli.automation.publish_decision_frame",
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
                    num_decisions=1,
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
                patch("cli.automa_cli.automation.create_vehicle_access", side_effect=lambda *_args, **_kw: SimpleNamespace(car=_FakeCar())),
                staged_runners(perception=mapper),
                patch(
                    "cli.automa_cli.automation.publish_decision_frame",
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
                    num_decisions=12,
                    take_control=False,
                )

            self.assertEqual(result.exit_code, 0, result.message)
            self.assertTrue(publications)
            first_frame_id, first_image = publications[0]
            self.assertEqual(first_frame_id, mapper.frame_ids[0])
            self.assertEqual(first_frame_id, "chase_frame_000100")
            self.assertIsNotNone(first_image)
            self.assertTrue(first_image[0])
