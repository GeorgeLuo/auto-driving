import unittest
from time import perf_counter
from unittest.mock import patch

import numpy as np

from autonomy.decision_cycle.perception.plugin import PerceptionPluginInputs
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID
from implementations.decision_cycle.perception.feeds.camera import CameraFrame
from implementations.decision_cycle.perception.plugins.floor_plane import plugin
from implementations.decision_cycle.perception.plugins.floor_plane.model import FloorPlaneConfig, source_obstacle_hits


class FloorPlaneTests(unittest.TestCase):
    def test_working_resolution_preserves_source_dimensions_and_normalized_boundary(self):
        rgb = np.full((480, 640, 3), (120, 100, 80), dtype=np.uint8)
        rgb[:260] = (20, 90, 160)
        rgb[260:325, 230:390] = (180, 60, 40)
        frame = CameraFrame(FRONT_CAMERA_SENSOR_ID, 0, rgb)
        inputs = PerceptionPluginInputs("test-frame", 0, {"frame": frame})
        with patch.object(plugin.cv2, "resize", wraps=plugin.cv2.resize) as resize:
            downscaled = plugin.FloorPlanePlugin().perceive(inputs).measurements
            self.assertEqual(resize.call_args.args[1], (320, 240))
            full = plugin.FloorPlanePlugin(config=FloorPlaneConfig(working_width=0)).perceive(inputs).measurements
            self.assertEqual(resize.call_count, 1)
        self.assertEqual((downscaled["image_width_px"], downscaled["image_height_px"]), (640, 480))
        self.assertTrue(downscaled["boundaries"])
        self.assertEqual(len(downscaled["boundaries"]), len(full["boundaries"]))
        for down, reference in zip(downscaled["boundaries"], full["boundaries"], strict=True):
            np.testing.assert_allclose(down["bbox_xyxy_norm"], reference["bbox_xyxy_norm"], atol=0.02)

        # A generous regression ceiling leaves room for shared CI contention.
        detector = plugin.FloorPlanePlugin()
        timings = []
        for _ in range(5):
            start = perf_counter()
            detector.perceive(inputs)
            timings.append(perf_counter() - start)
        self.assertLess(sorted(timings)[2], 0.15)

    def test_first_hit_requires_accumulated_floor_and_a_sustained_non_floor_run(self):
        floor = np.zeros((12, 6), dtype=bool)
        floor[[10, 8, 7, 6], 1] = True
        floor[[10, 8], 2] = True
        floor[[11, 10, 7], 3] = True
        config = FloorPlaneConfig(horizon_ratio=0.0, far_width_ratio=1.0, obstacle_run_length=2,
                                  min_floor_support=3, boundary_dilate_radius=0)
        rows, cols = np.nonzero(source_obstacle_hits(floor, config))
        self.assertEqual(list(zip(rows, cols, strict=True)), [(5, 1), (6, 3)])
