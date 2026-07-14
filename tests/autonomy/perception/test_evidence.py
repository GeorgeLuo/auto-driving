from __future__ import annotations

import unittest

from autonomy.perception import (
    PERCEPTION_TEXT_SCHEMA,
    PerceivedThing,
    PerceptionSignal,
    PerceptionText,
    ViewLocation,
)


class PerceptionEvidenceTests(unittest.TestCase):
    def test_polygon_and_signal_survive_perception_serialization(self) -> None:
        polygon = ((0.1, 0.2), (0.7, 0.25), (0.6, 0.8), (0.2, 0.75))
        original = PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test-plugin",
            status="ok",
            lines=("signal id=ready value=true", "thing id=region"),
            signals=(PerceptionSignal("ready", True, source_plugin_id="fixture"),),
            things=(
                PerceivedThing(
                    thing_id="region",
                    kind="region_proposal",
                    label="region",
                    location=ViewLocation(
                        frame="image",
                        zone="center",
                        bbox_xyxy_norm=(0.1, 0.2, 0.7, 0.8),
                        polygon_xy_norm=polygon,
                    ),
                    confidence=0.8,
                    source_plugin_id="fixture",
                ),
            ),
        )

        restored = PerceptionText.from_dict(original.to_dict())

        self.assertEqual(restored.signals[0].signal_id, "ready")
        self.assertEqual(restored.signals[0].source_plugin_id, "fixture")
        self.assertEqual(restored.things[0].location.polygon_xy_norm, polygon)
        self.assertEqual(restored.things[0].location.bbox_xyxy_norm, (0.1, 0.2, 0.7, 0.8))


if __name__ == "__main__":
    unittest.main(verbosity=2)
