"""The three matching Chase v1 presets detect obstacles and still request forward."""

import unittest

import numpy as np

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle
from autonomy.decision_cycle.steps import decision_steps
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import preset_activation


class ChaseForwardPresetTests(unittest.TestCase):
    def test_three_presets_keep_forward_command_when_an_obstacle_appears(self):
        activations = {
            step: preset_activation(step, "chase-v1-detect-by-color")
            for step in ("perception", "proposal", "plan")
        }
        cycle = DecisionCycle(decision_steps(activations))
        shared_memory = {}
        counts = []
        for index, blocked in enumerate((False, True, True, True)):
            at_ms = 100 + index * 500
            rgb = np.full((120, 160, 3), (213, 197, 172), dtype=np.uint8)
            rgb[:45] = (215, 214, 211)
            if blocked:
                rgb[40:90, 50:110] = (158, 107, 56)
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
            self.assertEqual(result.plan.selected_candidate().plugin_id, "drive_forward")
            self.assertEqual(result.control.steering, 0.0)
            self.assertEqual(result.control.throttle, 0.60)
        self.assertEqual(counts[0], 0)
        self.assertGreater(counts[1], 0)
        self.assertIs(shared_memory["perception.view_stagnation"]["view_static"], True)
        signal = next(item for item in result.observation.signals if item["signal_id"] == "view_static")
        self.assertIs(signal["value"], True)
