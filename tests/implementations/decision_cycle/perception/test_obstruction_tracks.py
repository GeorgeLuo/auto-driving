from __future__ import annotations

import unittest

from autonomy.decision_cycle.perception.runner import PerceptionRunner
from implementations.decision_cycle.catalog import perception_algorithm_activation


class ObstructionTracksProductionTests(unittest.TestCase):
    def test_catalog_constructs_obstruction_observer(self) -> None:
        mapper = PerceptionRunner.from_activation(
            perception_algorithm_activation("obstruction_observer")
        )
        self.assertEqual(mapper.plugin_ids, ("frame", "floor_plane", "obstruction_tracks"))
        self.assertEqual(
            [plugin.plugin_id for plugin in mapper.plugins],
            ["frame", "floor_plane", "obstruction_tracks"],
        )
        self.assertEqual(
            mapper.plugin_configs["obstruction_tracks"]["max_missed_frames"],
            2,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
