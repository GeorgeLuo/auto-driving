from __future__ import annotations

import unittest

from autonomy.perception import PerceivedThing, ViewLocation
from autonomy.perception.mappers import PluginPerceptionMapper
from implementations.perception.catalog import PERCEPTION_ALGORITHMS
from implementations.perception.obstruction_tracks import MultiObstructionTracksPlugin


def _region(
    bbox: tuple[float, float, float, float],
    *,
    touches_lower_image: bool = False,
) -> PerceivedThing:
    return PerceivedThing(
        thing_id="region",
        kind="region_proposal",
        label="region",
        location=ViewLocation(frame="image", zone="mid_left", bbox_xyxy_norm=bbox),
        confidence=0.8,
        properties={
            "area_fraction": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
            "edge_density": 0.8,
            "rectangularity": 0.8,
            "touches_lower_image": touches_lower_image,
        },
    )


class ObstructionTracksProductionTests(unittest.TestCase):
    def test_catalog_constructs_obstruction_observer(self) -> None:
        config = PERCEPTION_ALGORITHMS["obstruction_observer"]["mapper_config"]
        mapper = PluginPerceptionMapper(**config)
        self.assertEqual(
            [plugin.plugin_id for plugin in mapper.plugins],
            ["frame-observation-v0", "floor-plane-v0", "multi-obstruction-tracks-v0"],
        )
        self.assertEqual(
            mapper.plugin_configs["obstruction_tracks"]["max_missed_frames"],
            2,
        )

    def test_lower_frame_floor_like_region_is_suppressed(self) -> None:
        plugin = MultiObstructionTracksPlugin()
        kept = plugin._filter_candidates(
            (
                _region((0.20, 0.18, 0.42, 0.62)),
                _region((0.05, 0.70, 0.95, 0.98), touches_lower_image=True),
            )
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].location.bbox_xyxy_norm, (0.20, 0.18, 0.42, 0.62))


if __name__ == "__main__":
    unittest.main(verbosity=2)
