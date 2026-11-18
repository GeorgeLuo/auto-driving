"""Synthetic checks for the optional run 6 fitter, independent of the recording."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/fit'))
from estimate import Registration, command_intervals, wrapped_delta
from geometry import Observations, Scene


class FitTests(unittest.TestCase):
    def test_landmark_beyond_pinhole_edge_is_visible_in_distorted_image(self):
        marks = {'anchors': {'corner': ['bed', 0, 0, 0, 0]}, 'segments': {},
                 'frames': {'0': {'seed': [0, 0, 0], 'frameId': 'test',
                                  'marks': {'corner': [600, 240]}, 'outsideRight': ['corner']}}}
        reg = Registration(Scene(marks), Observations(marks))
        camera = reg.scene.map['camera']
        camera.update(height=.1, pitchDownRadians=0, verticalFovDegrees=90, barrelDistortion=.5)
        # At unit depth, 1.5 m sideways projects 360 px from the pinhole centre.
        reg.points = np.array([[1., .1, 1.5]])
        pixels, in_raster, _ = reg.projection(reg.obs.seeds)
        self.assertTrue(in_raster[0])
        self.assertAlmostEqual(reg.outside_errors(reg.obs.seeds)[0], 640 - pixels[0, 0])
        camera['barrelDistortion'] = 0
        self.assertFalse(reg.projection(reg.obs.seeds)[1][0])
        self.assertEqual(reg.outside_errors(reg.obs.seeds)[0], 0)

    def test_short_report_intervals_accumulate_into_physics_ticks(self):
        command = {'authority': {'authorized_output': {'throttle': .2, 'steering': 0}}}
        records = [{'report': {'timestamp_ms': ms, 'cycle': {'action': command}}}
                   for ms in (0, 40, 80, 120)]
        with patch('estimate.document', side_effect=records):
            group, = command_intervals([Path(str(i)) for i in range(4)], [0, 3], 10)
        self.assertEqual([cmd['steps'] for cmd in group], [0, 1, 0])

    def test_yaw_prior_crosses_pi(self):
        a = np.array([0, 0, np.pi - .01])
        b = np.array([0, 0, -np.pi + .01])
        self.assertAlmostEqual(wrapped_delta(a, b)[2], -.02)


if __name__ == '__main__':
    unittest.main()
