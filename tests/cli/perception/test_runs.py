from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.perception import PERCEPTION_TEXT_SCHEMA, PerceptionText
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from cli.automa_cli import perception as perception_module
from cli.automa_cli.perception_evaluation import evaluate_perception_frames
from cli.automa_cli.perception_runs import (
    CommandResult,
    _source_image_paths,
    apply_perception_experiment,
    compare_perception_candidates,
    run_perception_experiment,
)
from cli.automa_cli.vehicle_access import VehicleAccess
from implementations.perception.catalog import PERCEPTION_PLUGIN_SPECS


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
    def test_apply_accepts_one_image_and_reports_candidate_overrides(self) -> None:
        class FakeCandidateMapper:
            init_args: tuple[str, dict[str, object] | None] | None = None

            def __init__(self, candidate_id, *, config_overrides=None):
                type(self).init_args = (candidate_id, config_overrides)
                self.candidate = types.SimpleNamespace(runs_dir=Path("unused"))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def reset(self):
                return None

            def report_descriptor(self):
                return {
                    "algorithm": "candidate:fixture",
                    "config": {"threshold": 0.7},
                }

            def perceive(self, _request):
                return PerceptionText(
                    schema=PERCEPTION_TEXT_SCHEMA,
                    plugin_id="fixture",
                    status="empty",
                    lines=(f"schema={PERCEPTION_TEXT_SCHEMA}",),
                    signals=(),
                    things=(),
                )

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            with patch(
                "cli.automa_cli.perception_runs.LabPerceptionMapper",
                FakeCandidateMapper,
            ):
                result = apply_perception_experiment(
                    image,
                    candidate_id="fixture",
                    candidate_config={"threshold": 0.7},
                    json_output=True,
                )

        self.assertEqual(result.exit_code, 0)
        report = json.loads(result.message)
        self.assertEqual(report["source"]["path"], str(image.resolve()))
        self.assertEqual(len(report["frames"]), 1)
        self.assertEqual(
            FakeCandidateMapper.init_args,
            ("fixture", {"threshold": 0.7}),
        )

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

    def test_apply_manifest_falls_back_to_archived_frame_copy(self) -> None:
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

    def test_startup_report_apply_preserves_before_after_order(self) -> None:
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
                    "cli.automa_cli.perception_runs.apply_perception_experiment",
                    return_value=CommandResult(0, json.dumps(report)),
                ) as apply_mock,
            ):
                result = compare_perception_candidates(Path(tmp), json_output=True)

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual([item["candidate"] for item in payload["results"]], ["one", "two"])
        self.assertEqual(apply_mock.call_count, 2)

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
