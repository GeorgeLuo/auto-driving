"""The forward baseline must ignore obstacle and memory changes."""

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.publication import write_observation
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.proposal.plugins.drive_forward.plugin import DriveForward


def _context(frame_id="frame_1", timestamp_ms=10, observation=None, memory=None):
    shared = memory
    if observation is not None:
        shared = {} if shared is None else shared
        write_observation(shared, observation)
    return DecisionFrameContext(
        frame_id=frame_id, frame_index=0, timestamp_ms=timestamp_ms, shared_memory=shared,
    )


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
                result = plugin.propose(_context(observation=observation))
                self.assertTrue(result.is_active("frame_1"))
                self.assertEqual(result.command.steering, 0.0)
                self.assertEqual(result.command.throttle, 0.60)
                self.assertEqual(result.command.gear, "forward")
                self.assertEqual(result.reason, "constant_forward_baseline")

    def test_configured_throttle_is_preserved_without_reading_memory(self):
        memory = {"unrelated": {"obstacles": "stop"}}
        result = DriveForward(throttle=0.25).propose(_context(frame_id="frame_2", timestamp_ms=20, memory=memory))
        self.assertEqual(result.command.throttle, 0.25)
        self.assertEqual(memory, {"unrelated": {"obstacles": "stop"}})
        self.assertEqual(result.frame_id, "frame_2")

    def test_invalid_forward_throttle_is_rejected_at_construction(self):
        for value in (0, -0.1, 1.1, 1e-12, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DriveForward(throttle=value)
