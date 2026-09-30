"""Packaged proposal inspection scenarios stay out of the generic decision CLI."""

from __future__ import annotations

import unittest

from implementations.decision.inspection import prepare_inspection_scenarios


class InspectionScenarioTests(unittest.TestCase):
    def test_repositions_only_accepted_image_evidence(self) -> None:
        memory = {
            "records": [
                {
                    "record_id": "r1",
                    "kind": "obstacle",
                    "location": {
                        "frame": "image",
                        "zone": "center",
                        "bbox_xyxy_norm": [0, 0, 1, 1],
                        "polygon_xy_norm": [[0, 0]],
                    },
                },
                {
                    "record_id": "r2",
                    "kind": "other",
                    "location": {"frame": "image", "zone": "center"},
                },
            ]
        }

        scenarios = prepare_inspection_scenarios(memory)

        self.assertEqual(scenarios["left"]["changed_record_ids"], ["r1"])
        self.assertEqual(scenarios["left"]["label"], "Left obstruction")
        self.assertEqual(scenarios["left"]["context"], "Obstruction on the left")
        left_location = scenarios["left"]["memory"]["records"][0]["location"]
        self.assertEqual(left_location["zone"], "left")
        self.assertIsNone(left_location["bbox_xyxy_norm"])
        self.assertEqual(scenarios["right"]["memory"]["records"][0]["location"]["zone"], "right")
        self.assertEqual(scenarios["left"]["memory"]["records"][1]["location"]["zone"], "center")
        self.assertEqual(memory["records"][0]["location"]["zone"], "center")

    def test_rejects_a_frame_with_no_accepted_evidence(self) -> None:
        with self.assertRaises(ValueError):
            prepare_inspection_scenarios({"records": []})


if __name__ == "__main__":
    unittest.main()
