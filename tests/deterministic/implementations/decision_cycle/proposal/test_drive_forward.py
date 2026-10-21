"""The forward baseline must ignore obstacle and memory changes."""

import unittest

from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.proposal.inputs import build_decision_data_source
from implementations.decision_cycle.proposal.plugins.drive_forward.plugin import DriveForward


class DriveForwardTests(unittest.TestCase):
    def test_obstacle_presence_and_unavailable_observation_do_not_change_command(self):
        plugin = DriveForward()
        for present in (False, True, None):
            with self.subTest(present=present):
                observation = None if present is None else Observation(
                    observation_id="observation_1", created_at_ms=10, sensor_frame={},
                    signals=({"signal_id": "obstacle_in_sight", "value": present,
                              "confidence": 1.0, "properties": {}},),
                )
                source = build_decision_data_source(
                    frame_id="frame_1", frame_index=0, timestamp_ms=10,
                    observation=observation, observation_configured=True,
                )
                result = plugin.propose(source, None)
                self.assertTrue(result.is_active("frame_1"))
                self.assertEqual(result.command.steering, 0.0)
                self.assertEqual(result.command.throttle, 0.60)
                self.assertEqual(result.command.gear, "forward")
                self.assertEqual(result.reason, "constant_forward_baseline")

    def test_configured_throttle_is_preserved_without_reading_memory(self):
        source = build_decision_data_source(frame_id="frame_2", frame_index=1, timestamp_ms=20)
        memory = {"unrelated": {"obstacles": "stop"}}
        result = DriveForward(throttle=0.25).propose(source, memory)
        self.assertEqual(result.command.throttle, 0.25)
        self.assertEqual(memory, {"unrelated": {"obstacles": "stop"}})
        self.assertEqual(result.frame_id, "frame_2")

    def test_invalid_forward_throttle_is_rejected_at_construction(self):
        for value in (0, -0.1, 1.1, 1e-12, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DriveForward(throttle=value)
