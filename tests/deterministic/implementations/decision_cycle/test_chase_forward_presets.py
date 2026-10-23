"""The matching Chase v1 presets drive into an obstacle, back up until the right is clear, then turn right.

The collision also starts memory's stop row, so the next obstacle ahead starts
the maneuver before contact. Memory's turn model places the box it sees, but
learns no speed from frames this far apart, and its approach model learns no
row times, so perception's closeness stays unknown.
"""

import unittest

import numpy as np

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle
from autonomy.decision_cycle.memory.publication import read_belief
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.execution import ControlApplication
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import preset_activation


# Pixel boxes (x1, y1, x2, y2) in a 160x120 frame whose floor starts at row 45.
NEAR = (50, 40, 110, 90)
BACKED_AWAY = (45, 45, 115, 70)  # lower edge above the right band's threshold
TURNING = (30, 45, 95, 72)  # moved left but still in the centre
PASSED = (10, 45, 45, 72)  # left of the centre
APPROACHING = (50, 45, 110, 80)  # ahead, short of the learned stop row
AT_STOP_ROW = (50, 45, 110, 100)  # ahead, past the learned stop row


class ChaseForwardPresetTests(unittest.TestCase):
    def test_presets_back_up_until_the_right_is_clear_then_turn_right(self):
        activations = {
            step: preset_activation(step, "chase-v1-detect-by-color")
            for step in ("perception", "memory", "proposal", "plan")
        }
        cycle = DecisionCycle(decision_steps(activations))
        shared_memory = {}
        counts = []
        selected = []
        frames = (None, NEAR, NEAR, NEAR, NEAR, BACKED_AWAY, TURNING, PASSED, APPROACHING, AT_STOP_ROW)
        for index, box in enumerate(frames):
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
            shared_memory[CONTROL_APPLICATIONS_KEY] = (
                *shared_memory.get(CONTROL_APPLICATIONS_KEY, ()),
                ControlApplication(result.context.frame_id, "autonomy", True, "test", result.control, at_ms),
            )
            counts.append(len(result.perception.things))
            self.assertEqual(result.plan.selector_id, "select_proposal")
            signals = {item["signal_id"]: item for item in result.observation.signals}
            selected.append((
                signals["view_static"]["value"], result.plan.selected_candidate().plugin_id,
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
            (False, "drive_forward", 0.0, 0.60),   # short of the stop row
            (False, "avoid_obstacle", 0.0, -0.60),  # stop row reached: back up before contact
        ])
        hypothesis = shared_memory["memory.stop_row.hypothesis"]
        self.assertEqual(read_belief(shared_memory, "stop_row.row").value, 0.79)
        self.assertEqual(hypothesis["counts"]["collision"], 1)
        self.assertEqual(read_belief(shared_memory, "stop_row.row").trials[0].predicted, 0.79)
        model = shared_memory["memory.turn_model.state"]
        self.assertEqual([(item["bearing"], item["in_view"]) for item in model["landmarks"].values()], [(0.0, True)])
        self.assertTrue(all(not samples for samples in model["samples"].values()))
        self.assertEqual(shared_memory["memory.approach_model.state"]["samples"], {})
        self.assertEqual(signals["closeness_ahead"]["properties"]["reason"], "not_learned")
