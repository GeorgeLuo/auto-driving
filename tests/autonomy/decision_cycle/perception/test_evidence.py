from __future__ import annotations

import json
import unittest

from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
)
from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionSignal,
    ViewLocation,
)


def _thing(
    *,
    thing_id: str = "region",
    confidence: float = 0.8,
    location: ViewLocation | None = None,
) -> PerceivedThing:
    return PerceivedThing(
        thing_id=thing_id,
        kind="region_proposal",
        label="region",
        location=location or ViewLocation(frame="image", zone="center"),
        confidence=confidence,
        properties={"appearance": {"tags": ["textured"]}},
        source_plugin_id="fixture",
    )


class ViewLocationTests(unittest.TestCase):
    def test_location_normalizes_valid_geometry_and_round_trips(self) -> None:
        polygon = ((0.1, 0.2), (0.7, 0.25), (0.6, 0.8), (0.2, 0.75))
        location = ViewLocation(
            frame="image",
            zone="center",
            bbox_xyxy_norm=(0.1, 0.2, 0.7, 0.8),
            polygon_xy_norm=polygon,
        )

        restored = ViewLocation.from_dict(location.to_dict())

        self.assertEqual(restored, location)
        self.assertEqual(restored.bbox_xyxy_norm, (0.1, 0.2, 0.7, 0.8))
        self.assertEqual(restored.polygon_xy_norm, polygon)


class EvidenceValueTests(unittest.TestCase):

    def test_signal_and_thing_clamp_finite_confidence(self) -> None:
        self.assertEqual(PerceptionSignal("ready", True, confidence=2.0).confidence, 1.0)
        self.assertEqual(PerceptionSignal("ready", True, confidence=-1.0).confidence, 0.0)
        self.assertEqual(_thing(confidence=2.0).confidence, 1.0)
        self.assertEqual(_thing(confidence=-1.0).confidence, 0.0)


class PerceptionEvidenceSerializationTests(unittest.TestCase):
    def test_evidence_survives_detached_perception_serialization(self) -> None:
        polygon = ((0.1, 0.2), (0.7, 0.25), (0.6, 0.8), (0.2, 0.75))
        original = PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test-plugin",
            status="ok",
            lines=("signal id=ready value=true", "thing id=region"),
            signals=(
                PerceptionSignal(
                    "ready",
                    True,
                    properties={"support": {"frames": [1, 2]}},
                    source_plugin_id="fixture",
                ),
            ),
            things=(
                _thing(
                    location=ViewLocation(
                        frame="image",
                        zone="center",
                        bbox_xyxy_norm=(0.1, 0.2, 0.7, 0.8),
                        polygon_xy_norm=polygon,
                    ),
                ),
            ),
        )

        serialized = original.to_dict()
        restored = PerceptionText.from_dict(serialized)

        json.dumps(serialized)
        self.assertEqual(restored.signals[0].signal_id, "ready")
        self.assertEqual(restored.signals[0].source_plugin_id, "fixture")
        self.assertEqual(restored.things[0].location.polygon_xy_norm, polygon)
        self.assertEqual(restored.things[0].location.bbox_xyxy_norm, (0.1, 0.2, 0.7, 0.8))

        serialized["signals"][0]["properties"]["support"]["frames"].append(3)
        serialized["things"][0]["properties"]["appearance"]["tags"].append("moving")

        self.assertEqual(original.signals[0].properties["support"]["frames"], [1, 2])
        self.assertEqual(restored.signals[0].properties["support"]["frames"], [1, 2])
        self.assertEqual(original.things[0].properties["appearance"]["tags"], ["textured"])
        self.assertEqual(restored.things[0].properties["appearance"]["tags"], ["textured"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
