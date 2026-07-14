from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

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
                with LabPerceptionMapper("fixture", timeout_s=10) as mapper:
                    mapper.reset()
                    result = mapper.perceive(build_perception_request(snapshot))

        self.assertEqual(result.schema, PERCEPTION_TEXT_SCHEMA)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.plugin_runs), 1)
        self.assertTrue(any(thing.kind == "sensor_frame" for thing in result.things))


if __name__ == "__main__":
    unittest.main(verbosity=2)
