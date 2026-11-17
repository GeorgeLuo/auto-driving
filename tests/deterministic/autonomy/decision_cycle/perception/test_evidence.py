from __future__ import annotations

import json
import unittest

from autonomy.fields import FieldError

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

    def test_missing_confidence_round_trips_as_none(self) -> None:
        thing = _thing(confidence=None)
        signal = PerceptionSignal("ready", True)
        self.assertIsNone(thing.confidence)
        self.assertIsNone(signal.confidence)
        with self.assertRaises(FieldError) as missing:
            PerceptionSignal.from_dict({"signal_id": "ready", "value": True})
        self.assertEqual(missing.exception.field_name, "confidence")
        self.assertEqual(PerceptionSignal.from_dict(signal.to_dict()), signal)
        restored = PerceivedThing.from_dict(thing.to_dict())
        self.assertIsNone(restored.confidence)
        self.assertEqual(restored, thing)

    def test_scalar_signal_values_remain_exact(self) -> None:
        for value in (None, True, False, 1, 0.5, "present", ""):
            with self.subTest(value=value):
                signal = PerceptionSignal("ready", value)
                restored = PerceptionSignal.from_dict(signal.to_dict())
                self.assertEqual(restored.value, value)
                self.assertIs(type(restored.value), type(value))

    def test_host_evidence_errors_name_nested_fields(self) -> None:
        payload = PerceptionText("perception_text", "fixture", "ok", (), (_thing(),)).to_dict()
        del payload["things"][0]["location"]["zone"]
        with self.assertRaises(FieldError) as invalid:
            PerceptionText.from_dict(payload)
        self.assertEqual(invalid.exception.field_name, "things.0.location.zone")


class PerceptionEvidenceSerializationTests(unittest.TestCase):
    def test_evidence_round_trips_through_perception_serialization(self) -> None:
        polygon = ((0.1, 0.2), (0.7, 0.25), (0.6, 0.8), (0.2, 0.75))
        original = PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test-plugin",
            status="ok",
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

        self.assertEqual(original.signals[0].properties["support"]["frames"], [1, 2])
        self.assertEqual(original.things[0].properties["appearance"]["tags"], ["textured"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
