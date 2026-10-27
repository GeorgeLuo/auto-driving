from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli.perception_evaluation import evaluate_perception_frames
from cli.automa_cli.perception_runs import (
    inspect_perception,
    perceive_sensor_frame,
)
from cli.automa_cli.workbench_source import normalize_image_directory
from implementations.decision_cycle.catalog import preset_activation, selection_activation


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

    def test_inspect_rejects_preset_and_plugins_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            both = inspect_perception(image, preset="visual_observer", plugins=["frame"])

        self.assertEqual(both.exit_code, 2)
        self.assertIn("preset or plugins, not both", both.message)

    def test_inspect_reports_the_preset_or_custom_selection_it_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            preset = inspect_perception(image, preset="visual_observer", json_output=True)
            custom = inspect_perception(image, plugins=["frame"], json_output=True)

        self.assertEqual(json.loads(preset.message)["perception"]["preset"], "visual_observer")
        self.assertEqual(json.loads(custom.message)["perception"]["preset"], "custom")
        self.assertEqual(json.loads(custom.message)["perception"]["plugins"], ["frame"])

    def test_inspect_restages_perception_from_frame_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new("RGB", (48, 32), (25, 35, 45)).save(root / "a.png")
            Image.new("RGB", (48, 32), (30, 40, 50)).save(root / "b.png")
            switched = selection_activation("perception", plugins=["frame"])
            manifest = {
                "source_id": "restage-source",
                "frames": [
                    {"image_path": "a.png"},
                    {
                        "image_path": "b.png",
                        "steps": {"perception": switched.to_payload()},
                    },
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = inspect_perception(root, json_output=True)

        self.assertEqual(result.exit_code, 0, result.message)
        report = json.loads(result.message)
        self.assertEqual(
            [run["plugin_id"] for run in report["frames"][0]["plugin_runs"]],
            ["frame", "floor_plane"],
        )
        self.assertEqual(
            [run["plugin_id"] for run in report["frames"][1]["plugin_runs"]],
            ["frame"],
        )

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

    def test_inspect_manifest_remaps_archived_frame_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            archived = frames / "frame_000000.png"
            Image.new("RGB", (8, 8), (10, 20, 30)).save(archived)
            manifest = {
                "run_dir": "/original/machine/run",
                "frames": [
                    {
                        "image_path": "/original/machine/run/frames/frame_000000.png",
                    }
                ]
            }

            (root / "report.json").write_text(json.dumps(manifest))
            paths = [frame.image_path for frame in normalize_image_directory(root).frames]

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

            (root / "report.json").write_text(json.dumps(manifest))
            paths = [frame.image_path for frame in normalize_image_directory(root).frames]

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

    def test_representation_health_checks_a_score_only_when_present(self) -> None:
        location = {"frame": "image", "zone": "center", "bbox_xyxy_norm": (0.2, 0.2, 0.6, 0.6)}
        unscored = {"thing_id": "region", "kind": "region_proposal", "location": location}
        out_of_range = {**unscored, "thing_id": "bad", "confidence": 1.5}
        frames = [
            {"status": "ok", "perception": {"things": (unscored, out_of_range)}},
        ]

        health = evaluate_perception_frames(frames)

        self.assertEqual(health["geometry"]["valid_records"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
