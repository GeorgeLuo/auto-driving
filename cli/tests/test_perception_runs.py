from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from autonomy.perception import PERCEPTION_TEXT_SCHEMA, build_perception_request
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.perception.catalog import PERCEPTION_MAPPER_SPEC, PERCEPTION_PLUGIN_SPECS
from cli.automa_cli import perception as perception_module
from cli.automa_cli import lab_plugins
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.lab_plugins import LabPerceptionMapper, candidate_status, discover_candidates
from cli.automa_cli.perception_runs import (
    CommandResult,
    _replay_image_paths,
    compare_perception_candidates,
    run_perception_experiment,
)
from cli.automa_cli.perception_evaluation import evaluate_perception_frames
from cli.automa_cli.vehicle_access import VehicleAccess
from lab.plugins.perception.fastsam.src.plugin import _proposal_thing, _region_proposals


class FakeFrameCar:
    def __init__(self) -> None:
        self.read_count = 0

    def read_sensors(self, request):
        self.read_count += 1
        path = request.front_camera_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (48, 32), (25 + self.read_count, 35, 45)).save(path)
        return SensorSnapshot(
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
    def test_staged_bundle_keeps_component_and_plugin_types_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "vehicle")
            sync_controller_bundle(bundle, output=None)
            mapper = perception_module._load_mapper(
                PERCEPTION_MAPPER_SPEC,
                {
                    "plugins": ["frame"],
                    "plugin_specs": {"frame": PERCEPTION_PLUGIN_SPECS["frame"]},
                },
                bundle_root=Path(bundle["root_dir"]),
            )
            snapshot = SensorSnapshot(
                read_id="staged-frame",
                readings={
                    FRONT_CAMERA_SENSOR_ID: SensorReading(
                        sensor_id=FRONT_CAMERA_SENSOR_ID,
                        sensor_kind="camera",
                        captured_at_ms=1,
                        value=np.zeros((24, 32, 3), dtype=np.uint8),
                    )
                },
                started_at_ms=1,
                completed_at_ms=1,
            )

            result = mapper.perceive(build_perception_request(snapshot))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.plugin_runs[0].status, "ok")
        self.assertEqual(result.signals[0].signal_id, "front_camera_available")

    def test_named_runtime_refreshes_plugin_definition_but_custom_runtime_is_preserved(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-test",
            "vehicle_kind": "chase-sim-ws",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://example.invalid/ws"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(perception_module, "RUNTIME_ROOT", Path(tmp)):
                first = perception_module.ensure_local_perception_runtime(
                    vehicle=vehicle,
                    algorithm="visual_observer",
                )
                activation_path = first["manifest_path"]
                stale = json.loads(activation_path.read_text(encoding="utf-8"))
                stale["perception"]["mapper_config"]["plugins"].append("vlm_prep")
                activation_path.write_text(json.dumps(stale), encoding="utf-8")

                refreshed = perception_module.ensure_local_perception_runtime(vehicle=vehicle)
                refreshed_plugins = refreshed["manifest"]["perception"]["mapper_config"]["plugins"]
                self.assertEqual(refreshed_plugins, ["frame", "floor_plane", "motion_tracks"])
                self.assertFalse(refreshed["refreshed"])

                custom = refreshed["manifest"]
                custom["perception"]["algorithm"] = "custom"
                custom["perception"]["mapper_config"]["plugins"] = ["frame"]
                activation_path.write_text(json.dumps(custom), encoding="utf-8")
                preserved = perception_module.ensure_local_perception_runtime(vehicle=vehicle)

        self.assertEqual(preserved["manifest"]["perception"]["algorithm"], "custom")
        self.assertEqual(preserved["manifest"]["perception"]["mapper_config"]["plugins"], ["frame"])

    def test_replay_manifest_falls_back_to_archived_frame_copy(self) -> None:
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

            paths = _replay_image_paths(root, manifest)

        self.assertEqual(paths, [archived.resolve()])

    def test_classical_candidate_emits_contract_regions_without_writing(self) -> None:
        rgb = np.zeros((60, 80, 3), dtype=np.uint8)
        rgb[:, :40] = (210, 35, 35)
        rgb[:, 40:] = (35, 75, 210)
        snapshot = SensorSnapshot(
            read_id="classical-frame",
            readings={
                FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=1,
                    value=rgb,
                    metadata={"color_space": "RGB"},
                )
            },
            started_at_ms=1,
            completed_at_ms=1,
        )
        mapper = PluginPerceptionMapper(
            plugins=["classical"],
            plugin_specs={
                "classical": (
                    "lab.plugins.perception.classical_regions.src.plugin:"
                    "ClassicalRegionPlugin"
                )
            },
            plugin_configs={
                "classical": {
                    "spatial_radius": 2,
                    "color_radius": 4,
                    "min_area_fraction": 0.05,
                }
            },
        )

        result = mapper.perceive(build_perception_request(snapshot))

        self.assertEqual(result.status, "ok")
        self.assertGreaterEqual(len(result.things), 2)
        self.assertTrue(all(thing.kind == "region_proposal" for thing in result.things))
        self.assertEqual(result.artifacts, {})

    def test_fastsam_mask_adapter_emits_generic_region_geometry(self) -> None:
        masks = np.zeros((2, 20, 30), dtype=bool)
        masks[0, 4:14, 5:20] = True
        masks[1, 0, 0] = True

        proposals = _region_proposals(
            masks,
            np.array([0.8, 0.9], dtype=np.float32),
            min_area_fraction=0.01,
            max_regions=8,
        )
        thing = _proposal_thing(0, proposals[0])

        self.assertEqual(len(proposals), 1)
        self.assertEqual(thing.kind, "region_proposal")
        self.assertEqual(thing.properties["evidence"], "fastsam_mask")
        self.assertEqual(thing.location.bbox_xyxy_norm, (0.17241, 0.21053, 0.65517, 0.68421))
        self.assertIsNotNone(thing.location.polygon_xy_norm)
        self.assertGreaterEqual(len(thing.location.polygon_xy_norm or ()), 4)
        self.assertNotIn("contour_xy_norm", thing.properties)

    def test_representation_health_rejects_malformed_boxes_without_crashing(self) -> None:
        malformed = {
            "thing_id": "malformed",
            "kind": "region_proposal",
            "confidence": 0.8,
            "location": {"frame": "image", "zone": "center", "bbox_xyxy_norm": (0.2, 0.3, 0.4)},
        }

        health = evaluate_perception_frames(
            [
                {"status": "ok", "perception": {"things": (malformed,)}},
                {"status": "ok", "perception": {"things": (malformed,)}},
            ]
        )

        self.assertEqual(health["geometry"]["valid_records"], 0)
        self.assertEqual(health["continuity"]["mean_match_fraction"], 0.0)

    def test_startup_report_replay_preserves_before_after_order(self) -> None:
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

            paths = _replay_image_paths(root, manifest)

        self.assertEqual([path.name for path in paths], ["00_before.png", "00_after.png"])

    def test_candidate_comparison_runs_every_ready_candidate(self) -> None:
        report = {
            "summary": {
                "failed_frames": 0,
                "thing_kinds": {"region_proposal": 2},
                "latency_ms": {"cold_start": 10.0, "steady_median": 5.0, "steady_p95": 6.0},
                "memory_mb": {"peak_rss": 20.0},
                "representation_health": {
                    "score": 0.8,
                    "continuity": {"mean_match_fraction": 0.7, "mean_matched_iou": 0.6},
                },
            },
            "run_dir": None,
            "review": None,
        }
        candidates = [types.SimpleNamespace(candidate_id="one"), types.SimpleNamespace(candidate_id="two")]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("cli.automa_cli.perception_runs.discover_candidates", return_value=candidates),
                patch("cli.automa_cli.perception_runs.candidate_status", return_value={"ready": True}),
                patch(
                    "cli.automa_cli.perception_runs.replay_perception_experiment",
                    return_value=CommandResult(0, json.dumps(report)),
                ) as replay,
            ):
                result = compare_perception_candidates(Path(tmp), json_output=True)

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual([item["candidate"] for item in payload["results"]], ["one", "two"])
        self.assertEqual(replay.call_count, 2)

    def test_representation_health_accepts_in_memory_tuple_things(self) -> None:
        thing = {
            "thing_id": "region",
            "kind": "region_proposal",
            "confidence": 0.8,
            "location": {"frame": "image", "zone": "center", "bbox_xyxy_norm": (0.2, 0.2, 0.6, 0.6)},
        }
        frames = [
            {"status": "ok", "perception": {"things": (thing,)}},
            {"status": "ok", "perception": {"things": (thing,)}},
        ]

        health = evaluate_perception_frames(frames)

        self.assertEqual(health["geometry"]["valid_records"], 2)
        self.assertEqual(health["continuity"]["mean_match_fraction"], 1.0)
        self.assertEqual(health["score"], 1.0)

    def test_isolated_candidate_worker_round_trips_stable_perception_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = root / "fixture"
            runtime_python = candidate_dir / ".venv" / "bin" / "python"
            runtime_python.parent.mkdir(parents=True)
            os.symlink(sys.executable, runtime_python)
            manifest = {
                "schema": "automa_lab_perception_plugin_v0",
                "id": "fixture",
                "name": "Fixture candidate",
                "description": "Test-only candidate using an existing lightweight plugin.",
                "plugin": {
                    "entrypoint": "implementations.perception.observation.plugin:FrameObservationPlugin",
                    "config": {},
                },
                "runtime": {"python": ".venv/bin/python"},
                "output": {"schema": PERCEPTION_TEXT_SCHEMA, "kind": "sensor_frame"},
            }
            (candidate_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
            image_path = root / "input.png"
            Image.new("RGB", (48, 32), (20, 40, 60)).save(image_path)
            snapshot = SensorSnapshot(
                read_id="fixture-frame",
                readings={
                    FRONT_CAMERA_SENSOR_ID: SensorReading(
                        sensor_id=FRONT_CAMERA_SENSOR_ID,
                        sensor_kind="camera",
                        captured_at_ms=1,
                        path=str(image_path),
                    )
                },
                started_at_ms=1,
                completed_at_ms=1,
            )

            with patch.object(lab_plugins, "LAB_PERCEPTION_ROOT", root):
                candidates = discover_candidates()
                self.assertEqual([item.candidate_id for item in candidates], ["fixture"])
                self.assertTrue(candidate_status(candidates[0])["ready"])
                with LabPerceptionMapper("fixture", timeout_s=10) as mapper:
                    mapper.reset()
                    result = mapper.perceive(build_perception_request(snapshot))

        self.assertEqual(result.schema, PERCEPTION_TEXT_SCHEMA)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.plugin_runs), 1)
        self.assertTrue(any(thing.kind == "sensor_frame" for thing in result.things))

    def test_flagless_run_prefers_simulator_and_uses_vehicle_sensor_contract(self) -> None:
        fake_car = FakeFrameCar()
        discovery = {
            "vehicles": [
                {"vehicle_id": "piracer", "provider": "picar"},
                {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"},
            ],
            "active_count": 2,
            "inactive": [],
        }
        mapper = PluginPerceptionMapper(
            plugins=["frame"],
            plugin_specs={"frame": PERCEPTION_PLUGIN_SPECS["frame"]},
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = {
                "bundle": {
                    "root_dir": str(root / "bundle"),
                    "runtime_dir": str(root / "bundle" / "runtime"),
                },
                "manifest": {
                    "perception": {
                        "algorithm": "lightweight_observer",
                        "mapper_spec": "unused:test",
                        "mapper_config": {},
                    }
                },
                "source": {"tree_sha256": "test-tree"},
                "refreshed": False,
            }
            with (
                patch("cli.automa_cli.perception_runs.discover_active_vehicles", return_value=discovery),
                patch("cli.automa_cli.perception_runs.ensure_local_perception_runtime", return_value=runtime),
                patch("cli.automa_cli.perception_runs._load_mapper", return_value=mapper),
                patch(
                    "cli.automa_cli.perception_runs.create_vehicle_access",
                    return_value=VehicleAccess(
                        car=fake_car,
                        image_extension="png",
                        front_camera_endpoint="frame",
                    ),
                ),
            ):
                result = run_perception_experiment(frames=2, interval_s=0, json_output=True)

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(payload["source"]["vehicle_id"], "chase-sim-chaser")
        self.assertIn("simulator preferred", payload["source"]["selection"])
        self.assertEqual(payload["summary"]["frames"], 2)
        self.assertEqual(fake_car.read_count, 2)
        self.assertFalse(payload["recording"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
