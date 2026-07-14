from __future__ import annotations

import unittest

import numpy as np

from autonomy.perception import build_perception_request
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from lab.plugins.perception.fastsam.src.plugin import _proposal_thing, _region_proposals


class PerceptionCandidateTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
