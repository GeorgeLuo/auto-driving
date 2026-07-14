from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from autonomy.perception import build_perception_request
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from cli.automa_cli import perception as perception_module
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from implementations.perception.catalog import PERCEPTION_MAPPER_SPEC, PERCEPTION_PLUGIN_SPECS


class PerceptionStagingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
