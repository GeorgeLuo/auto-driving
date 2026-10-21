"""The turn model learns how its own turn command moves landmarks, and carries them out of view."""

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.memory.plugins.turn_model.plugin import STATE_KEY, TurnModel

MANEUVER_KEY = "proposal.avoid_obstacle.maneuver"


class _Car:
    """Feeds one camera frame per decision to a ``TurnModel``.

    A box is ``(object_id, centre_x)`` or ``(object_id, centre_x, width)`` in
    0..1 image coordinates. ``phase`` is the maneuver the proposal stored last
    cycle, so it is the command that held since the previous frame.
    """

    def __init__(self, plugin=None, memory=None):
        self.plugin = plugin or TurnModel()
        self.memory = {} if memory is None else memory
        self.now = 1000

    def step(self, *boxes, phase=None, after=50, captured=True, plugin_id="sim_cardboard_permanence"):
        self.now += after
        if phase is None:
            self.memory.pop(MANEUVER_KEY, None)
        else:
            self.memory[MANEUVER_KEY] = {"phase": phase}
        things = []
        for box in boxes:
            object_id, centre, width = (*box, 0.2) if len(box) == 2 else box
            things.append({
                "source_plugin_id": plugin_id,
                "properties": {"object_id": object_id},
                "location": {"bbox_xyxy_norm": (centre - width / 2, 0.3, centre + width / 2, 0.6)},
            })
        readings = {"front_camera": {"captured_at_ms": self.now}} if captured else {}
        observation = Observation(
            observation_id=f"observation_{self.now}", created_at_ms=self.now,
            sensor_frame={"readings": readings}, things=tuple(things),
        )
        self.plugin.update(DecisionFrameContext(
            frame_id=f"frame_{self.now}", frame_index=self.now // 50, timestamp_ms=self.now + 5,
            shared_memory=self.memory,
        ), observation)
        return self.plugin.status(self.memory)


def _speeds(status):
    return {name: (summary["widths_per_s"], summary["samples"]) for name, summary in status["speeds"].items()}


def _bearings(status):
    return {item["object_id"]: (item["bearing"], item["in_view"]) for item in status["landmarks"]}


class TurnModelTests(unittest.TestCase):
    def test_nothing_is_learned_without_landmarks_or_capture_times(self):
        car = _Car()
        self.assertEqual(car.plugin.status(car.memory)["health"], "empty")
        car.step()
        car.step(phase="turning")
        car.step((1, 0.5), captured=False)
        status = car.step((1, 0.6), captured=False)
        self.assertEqual(status["health"], "healthy")
        self.assertEqual(status["record_count"], 0)
        self.assertEqual(status["landmarks"], [])

    def test_each_kind_of_interval_learns_its_own_speed(self):
        car = _Car()
        car.step((1, 0.5))
        car.step((1, 0.5))
        car.step((1, 0.55), phase="turning")
        car.step((1, 0.65), phase="turning")
        car.step((1, 0.75), phase="turning")
        car.step((1, 0.8))
        status = car.step((1, 0.8))
        self.assertEqual(_speeds(status), {
            "turn_start": (1.0, 1), "turning": (2.0, 2), "turn_end": (1.0, 1), "straight": (0.0, 2),
        })
        self.assertEqual(status["record_count"], 6)
        self.assertEqual(status["speeds"]["turning"]["p25"], 2.0)

    def test_an_interval_is_named_by_every_command_that_held_over_it(self):
        car = _Car()
        car.step((1, 0.5))
        car.step((1, 0.5))
        car.step((1, 0.5), phase="turning", captured=False)
        status = car.step((1, 0.6), phase="turning")  # straight, then turning
        self.assertEqual(_speeds(status)["turn_start"], (1.0, 1))
        status = car.step((1, 0.7), phase="turning")
        self.assertEqual(_speeds(status)["turning"], (2.0, 1))
        car.step((1, 0.7), captured=False)
        car.step((1, 0.7), phase="turning", captured=False)
        status = car.step((1, 0.775))  # turning, straight, turning, straight
        self.assertEqual(_speeds(status)["turn_end"], (0.5, 1))

    def test_a_sample_is_the_median_of_the_landmarks_seen_at_both_ends(self):
        car = _Car()
        car.step((1, 0.2, 0.1), (2, 0.5, 0.1), (3, 0.7, 0.1))
        status = car.step((1, 0.3, 0.1), (2, 0.615, 0.1), (3, 0.82, 0.1), (4, 0.5, 0.1), phase="turning")
        self.assertEqual(_speeds(status)["turn_start"], (2.3, 1))

    def test_slow_turning_is_not_learned_and_enough_in_a_row_is_a_stall(self):
        car = _Car()
        car.step((1, 0.3))
        car.step((1, 0.4), phase="turning")
        car.step((1, 0.5), phase="turning")
        status = car.step((1, 0.5), phase="turning")
        self.assertEqual((_speeds(status)["turning"], status["stalls"]), ((2.0, 1), {"count": 0, "last_at_ms": None}))
        status = car.step((1, 0.51), phase="turning")
        self.assertEqual(status["stalls"], {"count": 1, "last_at_ms": car.now})
        self.assertEqual(car.step((1, 0.51), phase="turning")["stalls"]["count"], 1)
        car.step((1, 0.61), phase="turning")
        car.step((1, 0.61), phase="turning")
        status = car.step((1, 0.61), phase="turning")
        self.assertEqual((_speeds(status)["turning"], status["stalls"]["count"]), ((2.0, 2), 2))

    def test_a_car_whose_first_turns_stall_still_learns_its_free_turn(self):
        car = _Car()
        car.step((1, 0.5))
        car.step((1, 0.5), phase="turning")
        for _ in range(3):
            car.step((1, 0.5), phase="turning")
        status = car.step((1, 0.6), phase="turning")
        self.assertEqual((_speeds(status)["turning"], status["stalls"]["count"]), ((0.0, 4), 0))
        car.step((1, 0.6), phase="turning")
        status = car.step((1, 0.6), phase="turning")
        self.assertEqual((_speeds(status)["turning"], status["stalls"]["count"]), ((0.0, 4), 1))

    def test_clipped_resized_and_other_plugins_boxes_are_not_measured(self):
        car = _Car()
        car.step((1, 0.5), (2, 0.3, 0.1), (3, 0.75))
        status = car.step((1, 0.6), (2, 0.5, 0.16), (3, 0.9), phase="turning")
        self.assertEqual(_speeds(status)["turn_start"], (2.0, 1))  # 2 has a new size, 3 reaches the edge
        self.assertEqual(_bearings(status), {1: (0.1, True), 2: (0.0, True), 3: (0.35, False)})
        self.assertEqual(_Car().step((1, 0.5), plugin_id="sim_cardboard_obstacle")["landmarks"], [])

    def test_a_repeated_frame_or_a_long_gap_is_not_measured(self):
        car = _Car()
        car.step((1, 0.5), (2, 0.3))
        car.step((1, 0.5), (2, 0.3))
        status = car.step((1, 0.7), (2, 0.3), after=0)
        self.assertEqual((status["record_count"], _bearings(status)[1]), (1, (0.0, True)))
        status = car.step((1, 0.7), after=251)
        self.assertEqual(status["record_count"], 1)
        self.assertEqual(_bearings(status), {1: (0.2, True)})  # 2 could not be carried across the gap

    def test_an_unseen_landmark_is_carried_by_the_view_then_by_the_learned_speed(self):
        car = _Car()
        car.step((1, 0.3), (2, 0.6))
        car.step((1, 0.3), (2, 0.6))
        car.step((1, 0.4), (2, 0.7), phase="turning")
        status = car.step((1, 0.5), phase="turning")
        self.assertEqual(_bearings(status), {1: (0.0, True), 2: (0.3, False)})
        status = car.step(phase="turning")
        self.assertEqual(_bearings(status), {1: (0.1, False), 2: (0.4, False)})
        self.assertEqual(status["landmarks"][1]["carried_ms"], 100)
        status = car.step()  # the turn ends: no turn_end speed yet, so straight's 0.0
        self.assertEqual(_bearings(status), {1: (0.1, False), 2: (0.4, False)})
        self.assertEqual([item["straight_ms"] for item in status["landmarks"]], [50, 50])

    def test_straight_driving_or_an_unknown_speed_forgets_unseen_landmarks(self):
        car = _Car(TurnModel(forget_straight_ms=100))
        car.step((1, 0.5), (2, 0.3))
        car.step((1, 0.5), (2, 0.3))
        self.assertEqual(_bearings(car.step((1, 0.5))), {1: (0.0, True), 2: (-0.2, False)})
        self.assertIn(2, _bearings(car.step((1, 0.5))))
        self.assertNotIn(2, _bearings(car.step((1, 0.5))))
        car = _Car()
        car.step((1, 0.5))
        self.assertEqual(car.step(phase="turning")["landmarks"], [])

    def test_a_landmark_seen_again_checks_where_it_was_carried(self):
        def carried():
            car = _Car()
            car.step((1, 0.5), (2, 0.3))
            car.step((1, 0.5), (2, 0.3))
            car.step((1, 0.6), (2, 0.4), phase="turning")
            car.step((1, 0.7), phase="turning")  # 2 carried to 0.0
            return car

        status = carried().step((1, 0.8), (2, 0.62), phase="turning")  # carried on to 0.1
        self.assertEqual(status["positions"], {"checked": 1, "median_abs_error": 0.02, "median_carried_ms": 100})
        self.assertEqual(_bearings(status), {1: (0.3, True), 2: (0.12, True)})
        status = carried().step((1, 0.8), (3, 0.58), phase="turning")  # a new identity in its place
        self.assertEqual(status["positions"]["checked"], 1)
        self.assertEqual(_bearings(status), {1: (0.3, True), 3: (0.08, True)})
        for box in ((3, 0.58, 0.4), (3, 0.76)):  # a different size, or too far
            with self.subTest(box=box):
                status = carried().step((1, 0.8), box, phase="turning")
                self.assertEqual(status["positions"]["checked"], 0)
                self.assertEqual(set(_bearings(status)), {1, 2, 3})

    def test_samples_and_landmarks_are_bounded(self):
        car = _Car(TurnModel(max_samples=2, max_landmarks=2))
        car.step((1, 0.3), (2, 0.5))
        car.step((1, 0.3), (2, 0.5))
        status = car.step((2, 0.5), (3, 0.7))
        self.assertEqual(set(_bearings(status)), {2, 3})
        status = car.step((2, 0.5), (3, 0.7))
        self.assertEqual(_speeds(status)["straight"], (0.0, 2))

    def test_the_model_continues_across_fresh_instances_and_reset_clears_it(self):
        memory = {"unrelated": 1}
        car = _Car(memory=memory)
        car.step((1, 0.5))
        car.step((1, 0.5))
        car.plugin = TurnModel()
        self.assertEqual(_speeds(car.step((1, 0.5)))["straight"], (0.0, 2))
        TurnModel().reset(memory)
        self.assertEqual(memory, {"unrelated": 1})
        status = TurnModel().status(memory)
        self.assertEqual((status["health"], status["record_count"], status["landmarks"]), ("empty", 0, []))
        TurnModel().reset(None)

    def test_a_malformed_stored_model_keeps_only_its_valid_parts(self):
        landmark = {"bearing": 0.1, "width": 0.2, "in_view": True, "seen_at_ms": 1, "carried_ms": 0, "straight_ms": 0}
        memory = {STATE_KEY: {
            "samples": {"turning": [2.0, "fast", float("nan")], "straight": "none"},
            "landmarks": {"x": landmark, "5": landmark, "6": {**landmark, "width": 0}},
            "position_errors": [[0.1, 50], "bad"], "stall_count": True,
        }}
        status = TurnModel().status(memory)
        self.assertEqual(_speeds(status)["turning"], (2.0, 1))
        self.assertEqual(_bearings(status), {5: (0.1, True)})
        self.assertEqual(status["positions"]["median_abs_error"], 0.1)
        self.assertEqual(status["stalls"]["count"], 0)

    def test_without_shared_memory_the_update_fails(self):
        with self.assertRaises(ValueError):
            TurnModel().update(DecisionFrameContext(frame_id="f", frame_index=0, timestamp_ms=1), None)

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"landmark_plugin_id": ""}, {"sensor_id": None}, {"maneuver_key": 3}, {"turn_phase": ""},
            {"max_samples": 0}, {"max_interval_ms": 1.5}, {"stall_intervals": True}, {"forget_straight_ms": -1},
            {"max_landmarks": 0}, {"edge_margin": 0.5}, {"stall_fraction": 1.0}, {"stall_fraction": float("nan")},
            {"reacquire_within": -0.1}, {"max_size_change": 0.9}, {"max_size_change": True},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                TurnModel(**config)
