from __future__ import annotations

import json
import unittest

from autonomy.runtime import AutonomyControl


class AutonomyControlTests(unittest.TestCase):
    def test_control_normalizes_finite_movement_and_confidence(self) -> None:
        high = AutonomyControl(
            steering=2.5,
            throttle=3.0,
            confidence=1.5,
            reason="bounded-high",
        )
        low = AutonomyControl(
            steering=-2.5,
            throttle=-3.0,
            confidence=-0.5,
            reason="bounded-low",
        )

        self.assertEqual(
            (high.steering, high.throttle, high.confidence),
            (1.0, 1.0, 1.0),
        )
        self.assertEqual(
            (low.steering, low.throttle, low.confidence),
            (-1.0, -1.0, 0.0),
        )

    def test_control_rejects_non_finite_output_values(self) -> None:
        for field_name in ("steering", "throttle", "confidence"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field_name, value=value):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        AutonomyControl(**{field_name: value})

    def test_control_serialization_has_stable_shape_and_detached_metadata(self) -> None:
        control = AutonomyControl(
            steering=0.25,
            throttle=-0.5,
            confidence=0.75,
            reason="test-control",
            metadata={"planner": {"candidates": ["left", "right"]}},
        )

        serialized = control.to_dict()

        self.assertEqual(
            serialized,
            {
                "steering": 0.25,
                "throttle": -0.5,
                "confidence": 0.75,
                "reason": "test-control",
                "metadata": {"planner": {"candidates": ["left", "right"]}},
            },
        )
        json.dumps(serialized)

        serialized["metadata"]["planner"]["candidates"].append("center")
        self.assertEqual(
            control.metadata["planner"]["candidates"],
            ["left", "right"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
