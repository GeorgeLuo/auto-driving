"""Collision recovery backs up until the right is clear, then turns right until ahead is clear.

A box ahead that perception calls near turns toward a side with more reach, while that side has more.
"""

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.beliefs import Belief, Check
from autonomy.decision_cycle.memory.publication import belief_key
from autonomy.decision_cycle.observation.publication import OBSERVATION_KEY, write_observation
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.proposal.plugins.avoid_obstacle.plugin import AvoidObstacle

KEY = "proposal.avoid_obstacle.maneuver"
STOP_ROW_KEY = belief_key("stop_row.row")
COLLISION = {"view_static": True, "obstacle_in_sight": True}
NEAR_300 = ("near", 300)


def _backing_up(static_cycles):
    return {KEY: {"phase": "backing_up", "static_cycles": static_cycles, "cause": "collision"}}


TURNING = {KEY: {"phase": "turning", "cause": "collision"}}


def _reach_turn(direction):
    return {"phase": "turning", "cause": "reach", "direction": direction}


def _bands(ahead, left, right):
    """Closeness signals; a band is ``(value, reach_ms)``, or None with nothing in it."""

    return {"closeness_ahead": ahead, "closeness_left": left, "closeness_right": right}


def _closeness_signal(signal_id, band):
    if band is None:
        return {"signal_id": signal_id, "value": None, "confidence": None,
                "properties": {"reach_ms": None, "reason": "nothing_in_band"}}
    value, reach = band
    return {"signal_id": signal_id, "value": value, "confidence": None,
            "properties": {"reach_ms": reach, "reason": "within_one_run" if value == "near" else "beyond_one_run"}}


def _stop_row(value):
    return Belief("stop_row.row", value, "stop_row", 0)


def _step(memory, signals, plugin=None, ahead_row=None, closeness=None):
    observation = None if signals is None else Observation(
        observation_id="observation_1", created_at_ms=10, sensor_frame={},
        signals=tuple({"signal_id": signal_id, "value": value, "confidence": None,
                       "properties": {"nearest_bottom_y": ahead_row} if signal_id == "clearance_ahead" else {}}
                      for signal_id, value in signals.items())
        + tuple(_closeness_signal(signal_id, band) for signal_id, band in (closeness or {}).items()),
    )
    write_observation(memory, observation)
    result = (plugin or AvoidObstacle()).propose(DecisionFrameContext(
        frame_id="frame_1", frame_index=0, timestamp_ms=10, shared_memory=memory,
    ))
    memory.pop(OBSERVATION_KEY, None)
    return result


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

    def test_reaching_the_learned_stop_row_backs_up_before_a_collision(self):
        memory = {STOP_ROW_KEY: Belief("stop_row.row", .8, "stop_row", 1)}
        result = _step(memory, {"view_static": False, "clearance_ahead": False}, ahead_row=0.8)
        self.assertEqual(result.reason, "avoid_back_up_until_right_clear")
        self.assertEqual(result.command.throttle, -0.60)
        self.assertEqual(memory[KEY], {"phase": "backing_up", "static_cycles": 0, "cause": "stop_row"})
        self.assertEqual([(ref.kind, ref.id, ref.note) for ref in result.source_refs[:2]], [
            ("memory_record", KEY, "backing_up"), ("memory_record", STOP_ROW_KEY, "stop_row 0.8"),
        ])
        self.assertEqual(_step(memory, {"view_static": False}, ahead_row=0.9).source_refs[1].kind, "observation")

    def test_rows_short_of_the_stop_row_or_without_one_do_not_start_the_maneuver(self):
        for stored, ahead_row in (
            ({"stop_row": 0.8}, 0.79), ({"stop_row": 0.8}, None), ({"stop_row": None}, 0.9),
            ({"stop_row": True}, 0.9), (None, 0.9), ("0.8", 0.9),
        ):
            with self.subTest(stored=stored, ahead_row=ahead_row):
                memory = {} if stored is None else {STOP_ROW_KEY: Belief("stop_row.row", stored["stop_row"], "stop_row", 1) if isinstance(stored, dict) else stored}
                result = _step(memory, {"clearance_ahead": False}, ahead_row=ahead_row)
                self.assertEqual(result.reason, "no_collision")
                self.assertNotIn(KEY, memory)
        memory = {belief_key("elsewhere"): Belief("elsewhere", .5, "stop_row", 1)}
        result = _step(memory, {"clearance_ahead": False}, AvoidObstacle(stop_row_belief_id="elsewhere"), ahead_row=0.6)
        self.assertEqual(result.reason, "avoid_back_up_until_right_clear")

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

    def test_a_near_box_ahead_turns_toward_the_side_with_more_reach(self):
        memory = {STOP_ROW_KEY: _stop_row(0.6)}
        result = _step(memory, {"view_static": False, "clearance_ahead": False}, ahead_row=0.5,
                       closeness=_bands(NEAR_300, left=("near", 200), right=("far", 900)))
        self.assertEqual(result.reason, "avoid_turn_right_toward_reach")
        self.assertEqual((result.command.steering, result.command.throttle, result.command.gear),
                         (1.0, 0.60, "forward"))
        self.assertEqual(memory[KEY], _reach_turn("right"))
        self.assertEqual([(ref.kind, ref.note) for ref in result.source_refs],
                         [("memory_record", "turning"), ("observation", "clearance_and_collision_signals")])
        for left, right, direction in (
            (("far", 600), ("far", 400), "left"),
            (None, ("far", 900), "left"),
            (None, None, "right"),
            (("far", 600), ("far", 600), "right"),
            ((None, 400), ("near", 200), "left"),
        ):
            with self.subTest(left=left, right=right):
                memory = {}
                result = _step(memory, {}, ahead_row=0.5, closeness=_bands(NEAR_300, left=left, right=right))
                self.assertEqual(result.reason, f"avoid_turn_{direction}_toward_reach")
                self.assertEqual(result.command.steering, 1.0 if direction == "right" else -1.0)
                self.assertEqual(memory[KEY], _reach_turn(direction))

    def test_no_turn_unless_the_box_ahead_is_near_and_a_side_has_more_reach(self):
        for ahead, left, right in (
            (("far", 2000), None, None),
            ((None, 300), None, None),
            (None, None, None),
            (NEAR_300, ("near", 300), ("near", 200)),
            (NEAR_300, (None, None), (None, None)),
            (("near", None), None, None),
        ):
            with self.subTest(ahead=ahead, left=left, right=right):
                memory = {}
                result = _step(memory, {}, ahead_row=0.5, closeness=_bands(ahead, left=left, right=right))
                self.assertEqual(result.reason, "no_collision")
                self.assertEqual(memory, {})
        self.assertEqual(_step({}, {}, closeness={"closeness_ahead": NEAR_300}).reason, "no_collision")
        renamed = AvoidObstacle(closeness_ahead_id="a", closeness_left_id="l", closeness_right_id="r")
        bands = _bands(NEAR_300, left=None, right=None)
        self.assertEqual(_step({}, {}, renamed, closeness=bands).reason, "no_collision")
        self.assertEqual(_step({}, {}, renamed, closeness={"a": NEAR_300, "l": None, "r": ("far", 50)}).reason,
                         "avoid_turn_left_toward_reach")

    def test_a_reach_turn_lasts_while_its_side_has_more_reach_than_a_near_box_ahead(self):
        memory = {KEY: _reach_turn("right")}
        result = _step(memory, {}, ahead_row=0.5, closeness=_bands(NEAR_300, left=None, right=("near", 500)))
        self.assertEqual(result.reason, "avoid_turn_right_toward_reach")
        self.assertEqual(memory[KEY], _reach_turn("right"))
        self.assertEqual(_step(memory, None).reason, "no_collision")
        self.assertNotIn(KEY, memory)
        for ahead, left, right in (
            (("near", 500), None, ("near", 400)),
            (("near", 500), None, ("near", 500)),
            (("far", 2000), None, None),
            ((None, 300), None, None),
        ):
            with self.subTest(ahead=ahead, left=left, right=right):
                memory = {KEY: _reach_turn("right")}
                result = _step(memory, {}, ahead_row=0.5, closeness=_bands(ahead, left=left, right=right))
                self.assertEqual(result.reason, "no_collision")
                self.assertNotIn(KEY, memory)

    def test_a_collision_or_the_stop_row_backs_up_before_a_reach_turn(self):
        bands = _bands(NEAR_300, left=None, right=None)
        memory = {}
        _step(memory, COLLISION, closeness=bands)
        self.assertEqual(memory[KEY], {"phase": "backing_up", "static_cycles": 1, "cause": "collision"})
        memory = {STOP_ROW_KEY: _stop_row(0.6)}
        _step(memory, {"clearance_ahead": False}, ahead_row=0.6, closeness=bands)
        self.assertEqual(memory[KEY], {"phase": "backing_up", "static_cycles": 0, "cause": "stop_row"})

    def test_a_reach_turn_backs_up_at_the_stop_row_or_a_static_view(self):
        bands = _bands(NEAR_300, left=None, right=None)
        memory = {STOP_ROW_KEY: _stop_row(0.6), KEY: _reach_turn("left")}
        result = _step(memory, {"clearance_ahead": False}, ahead_row=0.6, closeness=bands)
        self.assertEqual(result.reason, "avoid_back_up_until_right_clear")
        self.assertEqual(memory[KEY], {"phase": "backing_up", "static_cycles": 0, "cause": "stop_row"})
        memory[KEY] = _reach_turn("left")
        _step(memory, {"view_static": True}, ahead_row=0.5, closeness=bands)
        self.assertEqual(memory[KEY], {"phase": "backing_up", "static_cycles": 1, "cause": "collision"})

    def test_the_turn_after_backing_up_still_ends_when_ahead_is_clear(self):
        memory = dict(TURNING)
        self.assertEqual(_step(memory, {"clearance_ahead": False}, closeness=_bands(("far", 2000), None, None)).reason,
                         "avoid_turn_right")
        self.assertEqual(_step(memory, {"clearance_ahead": True}, closeness=_bands(NEAR_300, None, ("far", 900))).reason,
                         "no_collision")
        self.assertEqual(memory, {})

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
        result = AvoidObstacle().propose(DecisionFrameContext(
            frame_id="frame_1", frame_index=0, timestamp_ms=10,
        ))
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
            {"max_static_cycles": 2.5}, {"stop_row_belief_id": ""}, {"stop_row_belief_id": None},
            {"closeness_ahead_id": ""}, {"closeness_left_id": None}, {"closeness_right_id": 3},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                AvoidObstacle(**config)

    def test_stop_confidence_survives_recovery_and_collision_replaces_its_cause(self):
        belief = _stop_row(.8).checked(Check(1, "stop", "support"), 64)
        memory = {STOP_ROW_KEY: belief}
        first = _step(memory, {"clearance_ahead": False}, ahead_row=.8)
        self.assertEqual(first.confidence, 2 / 3)
        self.assertEqual(_step(memory, {"clearance_right": True}).confidence, 2 / 3)
        self.assertEqual(memory[KEY]["cause"], "stop_row")
        self.assertEqual(_step(memory, {"view_static": True}).confidence, 1.0)
        self.assertEqual(memory[KEY]["cause"], "collision")

    def test_reach_turn_uses_both_comparison_signals_confidence_and_plan_passes_it_through(self):
        from implementations.decision_cycle.plan.plugins.select_proposal.plugin import SelectProposal
        memory = {}
        write_observation(memory, Observation("o", 10, {}, signals=(
            {"signal_id": "closeness_ahead", "value": "near", "confidence": .8,
             "properties": {"reach_ms": 300}},
            {"signal_id": "closeness_right", "value": "far", "confidence": .4,
             "properties": {"reach_ms": 900}},
        )))
        context = DecisionFrameContext("f", 0, 10, shared_memory=memory)
        proposal = AvoidObstacle().propose(context)
        self.assertEqual(proposal.confidence, .4)
        plan = SelectProposal(override_proposal_plugin_id="avoid_obstacle").plan(context, (proposal,))
        self.assertEqual(plan.confidence, .4)
        self.assertEqual(plan.to_dict()["confidence"], .4)
        memory[OBSERVATION_KEY] = Observation("o2", 20, {}, signals=(
            {"signal_id": "closeness_ahead", "value": "near", "confidence": None,
             "properties": {"reach_ms": 300}},
            {"signal_id": "closeness_right", "value": "far", "confidence": .4,
             "properties": {"reach_ms": 900}},
        ))
        self.assertIsNone(AvoidObstacle().propose(context).confidence)
