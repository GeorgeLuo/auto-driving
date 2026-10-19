"""Collision recovery backs up until the right is clear, then turns right until ahead is clear."""

import unittest

from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.proposal.inputs import build_decision_data_source
from implementations.decision_cycle.proposal.plugins.avoid_obstacle.plugin import AvoidObstacle

KEY = "proposal.avoid_obstacle.maneuver"
COLLISION = {"view_static": True, "obstacle_in_sight": True}


def _backing_up(static_cycles):
    return {KEY: {"phase": "backing_up", "static_cycles": static_cycles}}


TURNING = {KEY: {"phase": "turning"}}


def _source(signals=None, frame_id="frame_1"):
    observation = None if signals is None else Observation(
        observation_id="observation_1", created_at_ms=10, sensor_frame={},
        signals=tuple({"signal_id": signal_id, "value": value, "confidence": None, "properties": {}}
                      for signal_id, value in signals.items()),
    )
    return build_decision_data_source(
        frame_id=frame_id, frame_index=0, timestamp_ms=10,
        observation=observation, observation_configured=True,
    )


def _step(memory, signals, plugin=None):
    return (plugin or AvoidObstacle()).propose(_source(signals), memory)


class AvoidObstacleTests(unittest.TestCase):
    def test_idle_without_a_collision_is_inactive(self):
        for signals in ({}, {"view_static": True}, {"obstacle_in_sight": True},
                        {"view_static": False, "obstacle_in_sight": True},
                        {"view_static": True, "obstacle_in_sight": False, "wall_in_sight": None}, None):
            with self.subTest(signals=signals):
                memory = {}
                result = _step(memory, signals)
                self.assertFalse(result.is_active("frame_1"))
                self.assertIsNone(result.command)
                self.assertEqual(result.reason, "no_collision")
                self.assertEqual(memory, {})

    def test_collision_backs_up_straight_until_the_right_is_clear(self):
        memory = {}
        result = _step(memory, COLLISION)
        self.assertTrue(result.is_active("frame_1"))
        self.assertEqual(result.reason, "avoid_back_up_until_right_clear")
        self.assertEqual((result.command.steering, result.command.throttle, result.command.gear),
                         (0.0, -0.60, "reverse"))
        self.assertEqual(memory, _backing_up(1))
        for signals in ({"clearance_right": False, "clearance_ahead": True}, {"clearance_right": None}, {}):
            with self.subTest(signals=signals):
                self.assertEqual(_step(memory, signals).reason, "avoid_back_up_until_right_clear")
                self.assertEqual(memory, _backing_up(1))

    def test_backing_up_counts_static_cycles_until_it_cannot_reverse(self):
        memory = {}
        _step(memory, COLLISION)
        for _ in range(7):
            self.assertEqual(_step(memory, {"view_static": True}).reason, "avoid_back_up_until_right_clear")
        self.assertEqual(memory, _backing_up(8))
        _step(memory, {"view_static": False})
        self.assertEqual(memory, _backing_up(0))
        _step(memory, {"view_static": None})
        self.assertEqual(memory, _backing_up(0))
        for _ in range(8):
            _step(memory, {"view_static": True})
        self.assertEqual(memory, _backing_up(8))
        result = _step(memory, {"view_static": True, "clearance_right": False})
        self.assertEqual(result.reason, "avoid_turn_right")
        self.assertEqual(memory, TURNING)
        patient = AvoidObstacle(max_static_cycles=20)
        memory = _backing_up(8)
        self.assertEqual(_step(memory, {"view_static": True}, patient).reason, "avoid_back_up_until_right_clear")

    def test_a_wall_in_sight_is_also_a_collision(self):
        memory = {}
        result = _step(memory, {"view_static": True, "obstacle_in_sight": False, "wall_in_sight": True})
        self.assertEqual(result.reason, "avoid_back_up_until_right_clear")
        cardboard_only = AvoidObstacle(obstacle_signal_ids=["obstacle_in_sight"])
        self.assertEqual(_step({}, {"view_static": True, "wall_in_sight": True}, cardboard_only).reason,
                         "no_collision")

    def test_right_clearance_turns_right_until_ahead_is_clear(self):
        memory = _backing_up(3)
        result = _step(memory, {"clearance_right": True, "clearance_ahead": False, "view_static": True})
        self.assertEqual(result.reason, "avoid_turn_right")
        self.assertEqual((result.command.steering, result.command.throttle, result.command.gear),
                         (1.0, 0.60, "forward"))
        self.assertEqual(memory, TURNING)
        self.assertEqual(_step(memory, {"clearance_ahead": False, "view_static": False}).reason,
                         "avoid_turn_right")
        result = _step(memory, {"clearance_ahead": True})
        self.assertFalse(result.is_active("frame_1"))
        self.assertEqual(result.reason, "no_collision")
        self.assertEqual(memory, {})

    def test_static_view_while_turning_backs_up_again(self):
        memory = dict(TURNING)
        result = _step(memory, {"view_static": True, "clearance_ahead": True})
        self.assertEqual(result.reason, "avoid_back_up_until_right_clear")
        self.assertEqual(memory, _backing_up(1))

    def test_unavailable_observation_keeps_the_phase(self):
        for state, reason in ((_backing_up(2), "avoid_back_up_until_right_clear"), (TURNING, "avoid_turn_right")):
            with self.subTest(state=state):
                memory = dict(state)
                result = _step(memory, None)
                self.assertEqual(result.reason, reason)
                self.assertEqual(memory, state)
                self.assertEqual([ref.kind for ref in result.source_refs], ["memory_record"])

    def test_active_candidate_cites_its_memory_and_observation(self):
        result = _step({}, COLLISION)
        self.assertEqual([(ref.kind, ref.id) for ref in result.source_refs],
                         [("memory_record", KEY), ("observation", "observation_1")])

    def test_phase_continues_across_fresh_instances_and_reset_clears_it(self):
        memory = {"unrelated": 1}
        _step(memory, COLLISION)
        self.assertEqual(_step(memory, {}, AvoidObstacle()).reason, "avoid_back_up_until_right_clear")
        AvoidObstacle().reset(memory)
        self.assertEqual(memory, {"unrelated": 1})
        AvoidObstacle().reset(None)

    def test_without_shared_memory_is_inactive(self):
        result = AvoidObstacle().propose(_source(COLLISION), None)
        self.assertFalse(result.is_active("frame_1"))
        self.assertEqual(result.reason, "no_shared_memory")

    def test_configured_commands_are_used(self):
        plugin = AvoidObstacle(throttle=0.3, reverse_throttle=-0.4, turn_steering=0.5)
        memory = {}
        self.assertEqual(_step(memory, COLLISION, plugin).command.throttle, -0.4)
        command = _step(memory, {"clearance_right": True}, plugin).command
        self.assertEqual((command.steering, command.throttle), (0.5, 0.3))

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"throttle": 0}, {"throttle": 1.1}, {"throttle": float("nan")},
            {"reverse_throttle": 0.5}, {"reverse_throttle": -1.1},
            {"turn_steering": 0}, {"turn_steering": -1.0}, {"turn_steering": 1.5},
            {"obstacle_signal_ids": []}, {"obstacle_signal_ids": "wall_in_sight"},
            {"obstacle_signal_ids": [None]}, {"max_static_cycles": 0}, {"max_static_cycles": True},
            {"max_static_cycles": 2.5},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                AvoidObstacle(**config)
