from __future__ import annotations

from copy import deepcopy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from tests.implementations.perception.test_shared_memory_contract import plain, semantic

from autonomy.perception import PERCEPTION_TEXT_SCHEMA, build_perception_request
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from cli.automa_cli import lab_plugins
from cli.automa_cli.lab_plugins import LabPerceptionMapper, candidate_status, discover_candidates


class CandidateWorkerIntegrationTests(unittest.TestCase):
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
                with self.assertRaisesRegex(
                    ValueError,
                    "unknown candidate parameter.*configurable parameters: none",
                ):
                    LabPerceptionMapper(
                        "fixture",
                        config_overrides={"unknown": 1},
                    )
                with LabPerceptionMapper("fixture", timeout_s=10) as mapper:
                    mapper.reset()
                    result = mapper.perceive(build_perception_request(snapshot))

        self.assertEqual(result.schema, PERCEPTION_TEXT_SCHEMA)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.plugin_runs), 1)
        self.assertTrue(any(thing.kind == "sensor_frame" for thing in result.things))


    def test_worker_recreation_and_reset_use_the_callers_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = root / "motion"
            runtime_python = candidate_dir / ".venv" / "bin" / "python"
            runtime_python.parent.mkdir(parents=True)
            os.symlink(sys.executable, runtime_python)
            (candidate_dir / "plugin.json").write_text(json.dumps({
                "schema": "automa_lab_perception_plugin_v0", "id": "motion", "name": "Motion",
                "plugin": {
                    "entrypoint": "implementations.perception.motion.tracks:MotionTracksPlugin",
                    "config": {"max_features": 30, "search_radius": 6, "min_group_size": 4},
                },
                "runtime": {"python": ".venv/bin/python"},
                "output": {"schema": PERCEPTION_TEXT_SCHEMA, "kind": "scene_tracks"},
            }))
            rgb = np.random.default_rng(7).integers(0, 256, (72, 96, 3), dtype=np.uint8)
            image_path = root / "input.png"
            Image.fromarray(rgb).save(image_path)
            snapshot = SensorSnapshot(
                read_id="frame", readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera", captured_at_ms=1,
                    path=str(image_path),
                )}, started_at_ms=1, completed_at_ms=1,
            )
            memory = {"other.plugin": np.array([1, 2, 3])}
            with patch.object(lab_plugins, "LAB_PERCEPTION_ROOT", root):
                with LabPerceptionMapper("motion", timeout_s=10) as mapper:
                    mapper.perceive(build_perception_request(snapshot, memory=memory))
                    copied = deepcopy(memory)
                    Image.fromarray(np.roll(rgb, 2, axis=1)).save(image_path)
                    reused = mapper.perceive(build_perception_request(snapshot, memory=memory))
                with LabPerceptionMapper("motion", timeout_s=10) as recreated:
                    result = recreated.perceive(build_perception_request(snapshot, memory=copied))
                    self.assertEqual(semantic(result), semantic(reused))
                    self.assertEqual(plain(memory), plain(copied))
                    self.assertIn("perception.motion-tracks-v0.history", memory)
                    recreated.reset(copied)
                    self.assertEqual(plain(copied), plain({"other.plugin": np.array([1, 2, 3])}))
                    result = recreated.perceive(build_perception_request(snapshot, memory=copied))
                    self.assertEqual(result.plugin_runs[-1].status, "warming_up")
                    absent = SensorSnapshot(read_id="absent", readings={}, started_at_ms=2, completed_at_ms=2)
                    with self.assertRaisesRegex(RuntimeError, "front camera unavailable"):
                        recreated.perceive(build_perception_request(absent, memory=copied))
                    self.assertNotIn("perception.motion-tracks-v0.history", copied)


if __name__ == "__main__":
    unittest.main(verbosity=2)
