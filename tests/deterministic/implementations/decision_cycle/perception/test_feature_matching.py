from __future__ import annotations

import unittest

import numpy as np

from implementations.decision_cycle.perception.shared.features.feature_tracking import match_keypoints


class FeatureMatchingTests(unittest.TestCase):
    def test_low_contrast_matching_agrees_with_direct_normalized_correlation(self):
        rng = np.random.default_rng(2026)
        # Low contrast on a bright background exercises cancellation in float32.
        first = 180 + rng.normal(0, 0.5, (42, 46)).astype(np.float32)
        second = np.roll(first, (2, -3), axis=(0, 1))
        points = [(8, 8), (20, 16), (38, 34), (6, 5)]
        result = match_keypoints(first, second, points, patch_radius=3, search_radius=5, min_score=0.7)
        self.assertEqual(len(result), len(points))
        for match in result:
            x, y = map(int, match.source)
            px, py = map(int, match.target)
            patch = first[y-3:y+4, x-3:x+4].astype(np.float64)
            candidate = second[py-3:py+4, px-3:px+4].astype(np.float64)
            patch -= patch.mean()
            candidate -= candidate.mean()
            expected = np.sum(patch * candidate) / np.sqrt(np.sum(patch**2) * np.sum(candidate**2))
            self.assertAlmostEqual(match.score, expected, places=5)
            self.assertEqual((px, py), (x - 3, y + 2))

    def test_uniform_template_has_no_match(self):
        image = np.full((20, 20), 127, dtype=np.float32)
        self.assertEqual(match_keypoints(image, image, [(10, 10)], patch_radius=2,
                                         search_radius=4, min_score=0), [])
