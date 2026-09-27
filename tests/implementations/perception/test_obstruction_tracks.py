from __future__ import annotations

import unittest

from autonomy.perception.mappers import PluginPerceptionMapper
from implementations.perception.catalog import PERCEPTION_ALGORITHMS


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
