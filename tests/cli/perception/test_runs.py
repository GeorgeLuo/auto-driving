from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli import perception as perception_module
from cli.automa_cli.memory import inspect_memory
from implementations.decision_cycle.catalog import preset_activation
from cli.automa_cli.perception_evaluation import evaluate_perception_frames
from cli.automa_cli.perception_runs import (
    _source_image_paths,
    inspect_perception,
    perceive_sensor_frame,
)
from cli.automa_cli.vehicle_access import VehicleAccess
from implementations.decision_cycle.catalog import step_plugins


class FakeFrameCar:
    def __init__(self) -> None:
        self.read_count = 0

    def read_sensors(self, request):
        self.read_count += 1
        path = request.front_camera_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (48, 32), (25 + self.read_count, 35, 45)).save(path)
        return SensorFrame(
            read_id=request.read_id,
            readings={
                FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=self.read_count,
                    path=str(path),
                )
            },
            started_at_ms=self.read_count,
            completed_at_ms=self.read_count,
        )


class PerceptionRunTests(unittest.TestCase):
    def test_inspect_runs_selected_catalog_plugins_on_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            result = inspect_perception(
                image,
                plugins=["frame", "classical_regions"],
                json_output=True,
            )

        self.assertEqual(result.exit_code, 0, result.message)
        report = json.loads(result.message)
        self.assertEqual(report["source"]["path"], str(image.resolve()))
        self.assertEqual(len(report["frames"]), 1)
        runs = report["frames"][0]["plugin_runs"]
        self.assertEqual([run["plugin_id"] for run in runs], ["frame", "classical_regions"])

    def test_inspect_rejects_conflicting_selection_and_live_options_with_a_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            both = inspect_perception(image, preset="visual_observer", plugins=["frame"])
            live_option = inspect_perception(image, frames=3, vehicle_id="piracer")

        self.assertEqual(both.exit_code, 2)
        self.assertIn("either --preset or --plugin", both.message)
        self.assertEqual(live_option.exit_code, 2)
        self.assertIn("--id, --frames", live_option.message)

    def test_inspect_reports_the_preset_or_custom_selection_it_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            preset = inspect_perception(image, preset="visual_observer", json_output=True)
            custom = inspect_perception(image, plugins=["frame"], json_output=True)

        self.assertEqual(json.loads(preset.message)["perception"]["preset"], "visual_observer")
        self.assertEqual(json.loads(custom.message)["perception"]["preset"], "custom")
        self.assertEqual(json.loads(custom.message)["perception"]["plugins"], ["frame"])

    def test_perceive_sensor_frame_returns_the_record_and_saves_results_only_on_request(self) -> None:
        runner = PerceptionRunner.from_activation(preset_activation("perception", "lightweight_observer"))
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "frame.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            sensor_frame = SensorFrame(
                read_id="frame_000000",
                readings={
                    FRONT_CAMERA_SENSOR_ID: SensorReading(
                        sensor_id=FRONT_CAMERA_SENSOR_ID,
                        sensor_kind="camera",
                        captured_at_ms=7,
                        path=str(image),
                    )
                },
                started_at_ms=7,
                completed_at_ms=7,
            )
            shared_memory: dict = {}
            common = dict(
                frame_id="frame_000000",
                frame_index=0,
                image_path=str(image),
                shared_memory=shared_memory,
                metadata={},
            )

            record, perception = perceive_sensor_frame(runner, sensor_frame, **common)
            self.assertEqual(record["perception"], perception.to_dict())
            self.assertEqual(record["status"], perception.status)
            self.assertEqual(record["captured_at_ms"], 7)
            self.assertEqual(list(Path(tmp).iterdir()), [image])

            result_dir = Path(tmp) / "results" / "frame_000000"
            record, perception = perceive_sensor_frame(
                runner, sensor_frame, result_dir=result_dir, started=time.perf_counter() - 5.0, **common
            )
            saved = json.loads((result_dir / "perception.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, json.loads(json.dumps(record)))
            self.assertEqual(
                (result_dir / "perception.txt").read_text(encoding="utf-8"), perception.text + "\n"
            )
            self.assertGreaterEqual(record["duration_ms"], 5000.0)

    def test_named_runtime_refreshes_plugin_definition_but_custom_runtime_is_preserved(
        self,
    ) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-test",
            "vehicle_kind": "chase-sim-ws",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://example.invalid/ws"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(perception_module, "RUNTIME_ROOT", Path(tmp)):
                first = perception_module.ensure_local_perception_runtime(vehicle=vehicle)
                activation_path = first["manifest_path"]
                stale = json.loads(activation_path.read_text(encoding="utf-8"))
                stale["metadata"]["preset"] = "visual_observer"
                stale["plugins"].append("vlm_prep")
                activation_path.write_text(json.dumps(stale), encoding="utf-8")

                refreshed = perception_module.ensure_local_perception_runtime(
                    vehicle=vehicle
                )
                refreshed_plugins = refreshed["manifest"]["plugins"]
                self.assertEqual(
                    refreshed_plugins, ["frame", "floor_plane", "motion_tracks"]
                )
                self.assertFalse(refreshed["refreshed"])

                custom = refreshed["manifest"]
                custom["metadata"]["preset"] = "custom"
                custom["plugins"] = ["frame"]
                activation_path.write_text(json.dumps(custom), encoding="utf-8")
                preserved = perception_module.ensure_local_perception_runtime(
                    vehicle=vehicle
                )

        self.assertEqual(preserved["manifest"]["metadata"]["preset"], "custom")
        self.assertEqual(preserved["manifest"]["plugins"], ["frame"])

    def test_explicit_selection_runs_without_restaging_the_vehicle(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-test",
            "vehicle_kind": "chase-sim-ws",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://example.invalid/ws"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(perception_module, "RUNTIME_ROOT", Path(tmp)):
                chosen = perception_module.ensure_local_perception_runtime(
                    vehicle=vehicle, plugins=["frame", "classical_regions"]
                )
                staged = json.loads(chosen["manifest_path"].read_text(encoding="utf-8"))
                kept = perception_module.ensure_local_perception_runtime(vehicle=vehicle)

        self.assertEqual(chosen["manifest"]["plugins"], ["frame", "classical_regions"])
        self.assertEqual(chosen["manifest"]["metadata"]["preset"], "custom")
        self.assertEqual(
            chosen["manifest"]["metadata"]["controller_bundle"]["release"],
            staged["metadata"]["controller_bundle"]["release"],
        )
        self.assertEqual(staged["metadata"]["preset"], "lightweight_observer")
        self.assertEqual(kept["manifest"]["metadata"]["preset"], "lightweight_observer")

    def test_inspect_manifest_falls_back_to_archived_frame_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            archived = frames / "frame_000000.png"
            Image.new("RGB", (8, 8), (10, 20, 30)).save(archived)
            manifest = {
                "frames": [
                    {
                        "image_path": "/original/machine/run/frames/frame_000000.png",
                    }
                ]
            }

            paths = _source_image_paths(root, manifest)

        self.assertEqual(paths, [archived.resolve()])

    def test_representation_health_rejects_malformed_boxes_without_crashing(
        self,
    ) -> None:
        malformed = {
            "thing_id": "malformed",
            "kind": "region_proposal",
            "confidence": 0.8,
            "location": {
                "frame": "image",
                "zone": "center",
                "bbox_xyxy_norm": (0.2, 0.3, 0.4),
            },
        }

        health = evaluate_perception_frames(
            [
                {"status": "ok", "perception": {"things": (malformed,)}},
                {"status": "ok", "perception": {"things": (malformed,)}},
            ]
        )

        self.assertEqual(health["geometry"]["valid_records"], 0)
        self.assertEqual(health["continuity"]["mean_match_fraction"], 0.0)

    def test_startup_report_inspect_preserves_before_after_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            after = frames / "00_after.png"
            before = frames / "00_before.png"
            Image.new("RGB", (8, 8), (20, 20, 20)).save(after)
            Image.new("RGB", (8, 8), (10, 10, 10)).save(before)
            manifest = {
                "results": [
                    {
                        "before_capture": {"path": str(before)},
                        "after_capture": {"path": str(after)},
                    }
                ]
            }

            paths = _source_image_paths(root, manifest)

        self.assertEqual(
            [path.name for path in paths], ["00_before.png", "00_after.png"]
        )

    def test_representation_health_accepts_in_memory_tuple_things(self) -> None:
        thing = {
            "thing_id": "region",
            "kind": "region_proposal",
            "confidence": 0.8,
            "location": {
                "frame": "image",
                "zone": "center",
                "bbox_xyxy_norm": (0.2, 0.2, 0.6, 0.6),
            },
        }
        frames = [
            {"status": "ok", "perception": {"things": (thing,)}},
            {"status": "ok", "perception": {"things": (thing,)}},
        ]

        health = evaluate_perception_frames(frames)

        self.assertEqual(health["geometry"]["valid_records"], 2)
        self.assertEqual(health["continuity"]["mean_match_fraction"], 1.0)
        self.assertEqual(health["score"], 1.0)

    def test_flagless_run_prefers_simulator_and_uses_vehicle_sensor_contract(
        self,
    ) -> None:
        fake_car = FakeFrameCar()
        discovery = {
            "vehicles": [
                {"vehicle_id": "piracer", "provider": "picar"},
                {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"},
            ],
            "active_count": 2,
            "inactive": [],
        }
        runner = PerceptionRunner.from_selection(
            plugins=["frame"],
            plugin_specs={"frame": step_plugins("perception")["frame"]["spec"]},
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = {
                "bundle": {
                    "root_dir": str(root / "bundle"),
                    "runtime_dir": str(root / "bundle" / "runtime"),
                },
                "manifest": {
                    **preset_activation("perception", "lightweight_observer").to_payload(),
                },
                "source": {"tree_sha256": "test-tree"},
                "refreshed": False,
            }
            with (
                patch(
                    "cli.automa_cli.perception_runs.discover_active_vehicles",
                    return_value=discovery,
                ),
                patch(
                    "cli.automa_cli.perception_runs.ensure_local_perception_runtime",
                    return_value=runtime,
                ),
                patch(
                    "cli.automa_cli.perception_runs.load_staged_runner", return_value=runner
                ),
                patch(
                    "cli.automa_cli.perception_runs.create_vehicle_access",
                    return_value=VehicleAccess(
                        car=fake_car,
                        image_extension="png",
                        front_camera_endpoint="frame",
                    ),
                ),
            ):
                result = inspect_perception(
                    frames=2, interval_s=0, json_output=True
                )
                inspect_root = root / "inspections"
                with patch("cli.automa_cli.perception_runs.INSPECT_ROOT", inspect_root):
                    recorded = json.loads(
                        inspect_perception(
                            frames=1, interval_s=0, record=True, json_output=True
                        ).message
                    )
                saved = json.loads(
                    (inspect_root / recorded["run_id"] / "report.json").read_text(encoding="utf-8")
                )
                # Memory inspect reads a live recording, as the README walks through.
                remembered = json.loads(
                    inspect_memory(str(inspect_root / recorded["run_id"]), json_output=True).message
                )

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(payload["source"]["vehicle_id"], "chase-sim-chaser")
        self.assertIn("simulator preferred", payload["source"]["selection"])
        self.assertEqual(payload["summary"]["frames"], 2)
        self.assertIsNone(payload["run_dir"])
        self.assertTrue(recorded["run_id"].startswith("inspect-chase-sim-chaser-"))
        self.assertEqual(saved["schema"], "perception_inspect_v0")
        self.assertEqual(saved["perception"]["preset"], "lightweight_observer")
        self.assertEqual(fake_car.read_count, 3)
        self.assertEqual(remembered["source"]["frame_count"], 1)
        self.assertEqual(remembered["perception"]["preset"], "lightweight_observer")
        self.assertEqual(remembered["perception"]["plugins"], saved["perception"]["plugins"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
