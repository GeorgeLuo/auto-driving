"""The approach model times lower edges stepping down one row while driving straight, and how long it drives before stopping."""

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.memory.plugins.approach_model.plugin import STATE_KEY, ApproachModel

MANEUVER_KEY = "proposal.avoid_obstacle.maneuver"
BINS = 120


class _Car:
    """Feeds one camera frame per decision to an ``ApproachModel``.

    An edge is ``(object_id, row)``: a box ahead whose lower edge is on
    ``row`` of 120. ``maneuver`` is what the proposal stored last cycle, and
    ``static`` is this frame's ``view_static``.
    """

    def __init__(self, plugin=None, memory=None):
        self.plugin = plugin or ApproachModel()
        self.memory = {} if memory is None else memory
        self.now = 1000

    def step(self, *edges, maneuver=None, static=False, unchanged_ms=0, after=50, x=(0.4, 0.6),
             plugin_id="sim_cardboard_permanence"):
        self.now += after
        if maneuver is None:
            self.memory.pop(MANEUVER_KEY, None)
        else:
            self.memory[MANEUVER_KEY] = maneuver
        things = tuple({
            "source_plugin_id": plugin_id,
            "properties": {} if object_id is None else {"object_id": object_id},
            "location": {"bbox_xyxy_norm": (x[0], 0.1, x[1], row / BINS)},
        } for object_id, row in edges)
        observation = Observation(
            observation_id=f"observation_{self.now}", created_at_ms=self.now,
            sensor_frame={"readings": {"front_camera": {"captured_at_ms": self.now}}}, things=things,
            signals=({"signal_id": "view_static", "value": static, "confidence": None,
                      "properties": {"unchanged_for_ms": unchanged_ms}},),
        )
        self.plugin.update(DecisionFrameContext(
            frame_id=f"frame_{self.now}", frame_index=self.now // 50, timestamp_ms=self.now + 5,
            shared_memory=self.memory,
        ), observation)
        return self.plugin.status(self.memory)


def _rows(status):
    return {item["row"]: (item["ms_per_row"], item["samples"]) for item in status["rows"]}


class ApproachModelTests(unittest.TestCase):
    def test_an_edge_stepping_one_row_times_the_row_it_left(self):
        car = _Car()
        car.step((1, 60))
        car.step((1, 60))
        self.assertEqual(_rows(car.step((1, 61))), {})
        car.step((1, 61), after=100)
        status = car.step((1, 62))
        self.assertEqual(_rows(status), {round(61 / BINS, 4): (150, 1)})
        self.assertEqual(status["edges"], 1)
        car.step((1, 63), after=200)
        self.assertEqual(_rows(car.step((1, 64), after=100)), {
            round(61 / BINS, 4): (150, 1), round(62 / BINS, 4): (200, 1), round(63 / BINS, 4): (100, 1),
        })

    def test_a_row_holds_the_lower_edges_from_its_top_to_the_next_row(self):
        car = _Car()
        for row in (60.9, 61.2, 62.1):
            status = car.step((1, row))
        self.assertEqual(_rows(status), {round(61 / BINS, 4): (50, 1)})

    def test_each_row_learns_the_median_of_its_samples(self):
        car = _Car(ApproachModel(max_samples=3))
        car.step((1, 60))
        for first, second in ((50, 50), (100, 150), (200, 250), (150, 100)):
            car.step((1, 61), after=first)
            car.step((1, 62), after=second)
            car.step((1, 60), after=50)
        status = car.plugin.status(car.memory)
        self.assertEqual(_rows(status), {round(61 / BINS, 4): (150, 3)})
        self.assertEqual(status["record_count"], 3)

    def test_jumps_turns_static_views_and_gaps_restart_the_clock(self):
        cases = {
            "jump": dict(edges=((1, 64),)),
            "turn": dict(edges=((1, 62),), maneuver={"phase": "turning"}),
            "backing up": dict(edges=((1, 62),), maneuver={"phase": "backing_up", "static_cycles": 0}),
            "gap": dict(edges=((1, 62),), after=300),
            "gone": dict(edges=()),
        }
        for name, step in cases.items():
            with self.subTest(name=name):
                car = _Car()
                car.step((1, 60))
                car.step((1, 61))
                status = car.step(*step.pop("edges"), **step)
                self.assertEqual(_rows(status), {})
                self.assertEqual(_rows(car.step((1, 62), after=50)), {})
        car = _Car()
        car.step((1, 60))
        car.step((1, 61), static=True, unchanged_ms=40)
        car.step((1, 62))
        self.assertEqual(_rows(car.step((1, 63))), {})

    def test_only_unclipped_edges_ahead_from_the_edge_plugins_are_timed(self):
        for name, step in {
            "left of the band": dict(x=(0.0, 0.3)), "right of the band": dict(x=(0.7, 1.0)),
            "other plugin": dict(plugin_id="sim_cardboard_obstacle"),
        }.items():
            with self.subTest(name=name):
                car = _Car()
                for row in (60, 61, 62):
                    status = car.step((1, row), **step)
                self.assertEqual((_rows(status), status["edges"]), ({}, 0))
        car = _Car()
        for row in (116, 117, 118):
            status = car.step((1, row))
        self.assertEqual(_rows(status), {round(117 / BINS, 4): (50, 1)})
        self.assertEqual(_rows(car.step((1, 119))), {round(117 / BINS, 4): (50, 1)})
        walls = _Car()
        for row in (60, 61, 62):
            status = walls.step((None, row), plugin_id="sim_wall")
        self.assertEqual(_rows(status), {round(61 / BINS, 4): (50, 1)})

    def test_a_straight_run_ending_in_a_stop_is_learned_without_its_static_time(self):
        car = _Car()
        for _ in range(4):
            car.step()
        status = car.step(maneuver={"phase": "backing_up", "static_cycles": 0})
        self.assertEqual((status["run_ms"], status["runs"]), (150, 1))
        car.step(maneuver={"phase": "turning"})
        for _ in range(6):
            car.step()
        car.step(static=True, unchanged_ms=120)
        status = car.step(maneuver={"phase": "backing_up", "static_cycles": 1})
        self.assertEqual((status["run_ms"], status["runs"]), (165, 2))

    def test_runs_ended_any_other_way_are_not_counted(self):
        for name, ending in {
            "turn while near": [dict(maneuver={"phase": "turning", "cause": "near"})],
            "drive backwards": [dict(static=True, unchanged_ms=40), dict()],
        }.items():
            with self.subTest(name=name):
                car = _Car()
                for _ in range(4):
                    car.step()
                for step in ending:
                    car.step(**step)
                status = car.step(maneuver={"phase": "backing_up", "static_cycles": 0})
                self.assertEqual((status["run_ms"], status["runs"]), (None, 0))

    def test_rows_learned_at_another_resolution_are_dropped_and_reset_clears(self):
        car = _Car()
        for row in (60, 61, 62):
            car.step((1, row))
        car.step(maneuver={"phase": "backing_up", "static_cycles": 0})
        coarse = ApproachModel(row_bins=60)
        status = coarse.status(car.memory)
        self.assertEqual((status["rows"], status["row_bins"], status["runs"]), ([], 60, 1))
        coarse.reset(car.memory)
        self.assertNotIn(STATE_KEY, car.memory)
        self.assertEqual(ApproachModel().status(car.memory)["health"], "empty")
        ApproachModel().reset(None)

    def test_shared_memory_is_required(self):
        with self.assertRaises(ValueError):
            ApproachModel().update(DecisionFrameContext(
                frame_id="frame_1", frame_index=1, timestamp_ms=1, shared_memory=None,
            ), None)

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"edge_plugin_ids": []}, {"edge_plugin_ids": "sim_wall"}, {"edge_plugin_ids": [None]},
            {"sensor_id": ""}, {"maneuver_key": None}, {"stop_phase": ""}, {"row_bins": 0},
            {"row_bins": True}, {"max_samples": 2.5}, {"max_interval_ms": -1}, {"min_x": -0.1},
            {"max_x": 1.2}, {"min_x": 0.6, "max_x": 0.4}, {"edge_margin": 0.6}, {"edge_margin": float("inf")},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                ApproachModel(**config)
