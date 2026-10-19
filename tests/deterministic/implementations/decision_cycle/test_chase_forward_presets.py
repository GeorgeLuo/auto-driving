"""The three matching Chase v1 presets drive into an obstacle, back up until the right is clear, then turn right."""

import unittest

import numpy as np

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle
from autonomy.decision_cycle.steps import decision_steps
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import preset_activation


# Pixel boxes (x1, y1, x2, y2) in a 160x120 frame whose floor starts at row 45.
NEAR = (50, 40, 110, 90)
BACKED_AWAY = (45, 45, 115, 70)  # lower edge above the right band's threshold
TURNING = (30, 45, 95, 72)  # moved left but still in the centre
PASSED = (10, 45, 45, 72)  # left of the centre


class ChaseForwardPresetTests(unittest.TestCase):
    def test_three_presets_back_up_until_the_right_is_clear_then_turn_right(self):
        activations = {
            step: preset_activation(step, "chase-v1-detect-by-color")
            for step in ("perception", "proposal", "plan")
        }
        cycle = DecisionCycle(decision_steps(activations))
        shared_memory = {}
        counts = []
        selected = []
        for index, box in enumerate((None, NEAR, NEAR, NEAR, NEAR, BACKED_AWAY, TURNING, PASSED)):
            at_ms = 100 + index * 500
            rgb = np.full((120, 160, 3), (213, 197, 172), dtype=np.uint8)
            rgb[:45] = (215, 214, 211)
            if box is not None:
                x1, y1, x2, y2 = box
                rgb[y1:y2, x1:x2] = (158, 107, 56)
            reading = SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
                captured_at_ms=at_ms, value=rgb, metadata={"color_space": "RGB"},
            )
            frame = SensorFrame(
                read_id=f"read_{index}", readings={reading.sensor_id: reading},
                started_at_ms=at_ms, completed_at_ms=at_ms,
            )
            result = cycle.run(DecisionFrameContext(
                frame_id=f"frame_{index}", frame_index=index, timestamp_ms=at_ms,
                sensor_frame=frame, shared_memory=shared_memory,
            ))
            counts.append(len(result.perception.things))
            self.assertEqual(result.plan.selector_id, "select_proposal")
            signal = next(item for item in result.observation.signals if item["signal_id"] == "view_static")
            selected.append((
                signal["value"], result.plan.selected_candidate().plugin_id,
                result.control.steering, result.control.throttle,
            ))
        self.assertEqual(counts[0], 0)
        self.assertGreater(counts[1], 0)
        self.assertEqual(selected, [
            (None, "drive_forward", 0.0, 0.60),
            (False, "drive_forward", 0.0, 0.60),
            (None, "drive_forward", 0.0, 0.60),
            (True, "avoid_obstacle", 0.0, -0.60),  # collision
            (True, "avoid_obstacle", 0.0, -0.60),  # the right is still blocked
            (False, "avoid_obstacle", 1.0, 0.60),  # right clear: turn right
            (False, "avoid_obstacle", 1.0, 0.60),  # still in the centre
            (False, "drive_forward", 0.0, 0.60),   # ahead clear
        ])
        self.assertNotIn("proposal.avoid_obstacle.maneuver", shared_memory)
