from __future__ import annotations

import unittest

from autonomy.decision_cycle.perception.runner import PerceptionRunner
from implementations.decision_cycle.catalog import preset_activation
from implementations.decision_cycle.perception.plugins.multi_obstruction_tracks.plugin import (
    MultiObstructionTracksPlugin,
)
from tests.implementations.decision_cycle.perception.tracks_replay import load_baseline, tracks_replay

PLUGIN_IDS = ["frame", "floor_plane", "multi_obstruction_tracks"]


class ObstructionObserverPresetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = PerceptionRunner.from_activation(
            preset_activation("perception", "obstruction_observer")
        )

    def test_catalog_constructs_obstruction_observer(self) -> None:
        self.assertEqual(self.mapper.plugin_ids, tuple(PLUGIN_IDS))
        self.assertEqual([plugin.plugin_id for plugin in self.mapper.plugins.values()], PLUGIN_IDS)
        self.assertEqual(
            self.mapper.plugin_configs["multi_obstruction_tracks"]["max_missed_frames"],
            2,
        )

    def test_replay_matches_the_tracks_the_removed_obstruction_tracks_plugin_gave(self) -> None:
        # The baseline was recorded from the retired ``obstruction_tracks`` plugin
        # under this preset's config; it includes frames where a track is held.
        plugin = MultiObstructionTracksPlugin(
            **self.mapper.plugin_configs["multi_obstruction_tracks"]
        )

        self.assertEqual(tracks_replay(plugin), load_baseline("obstruction_observer"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
