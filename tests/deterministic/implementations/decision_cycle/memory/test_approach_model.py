"""The approach model times lower edges stepping down one row while driving straight, and how long it drives before stopping."""

import unittest
from dataclasses import replace

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication


from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.beliefs import Belief
from autonomy.decision_cycle.memory.publication import belief_key, read_belief
from autonomy.decision_cycle.observation.publication import write_observation
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.memory.plugins.approach_model.plugin import STATE_KEY, ApproachModel

BINS = 120


class _Car:
    """Feeds one camera frame per decision to an ``ApproachModel``.

    An edge is ``(object_id, row)``: a box ahead whose lower edge is on
    ``row`` of 120. ``maneuver`` selects the applied command; ``static`` is this frame's
    ``view_static``.
    """

    def __init__(self, plugin=None, memory=None):
        self.plugin = plugin or ApproachModel()
        self.memory = {} if memory is None else memory
        self.now = 1000

    def step(self, *edges, maneuver=None, static=False, unchanged_ms=0, after=50, x=(0.4, 0.6),
             plugin_id="sim_cardboard_permanence", stop_reason="avoid_back_up_until_right_clear"):
        self.now += after
        phase_value = (maneuver or {}).get("phase")
        command = AutonomyControl(steering=1 if phase_value == "turning" else 0,
            throttle=-.6 if phase_value == "backing_up" else .6,
            reason=stop_reason if phase_value == "backing_up" else "drive")
        applications = self.memory.get(CONTROL_APPLICATIONS_KEY, ())
        at = self.now - after + 1 if after > 0 else self.now
        self.memory[CONTROL_APPLICATIONS_KEY] = (*applications, ControlApplication(
            None, "autonomy", True, "test", command, at))[-32:]
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
        write_observation(self.memory, observation)
        self.plugin.update(DecisionFrameContext(
            frame_id=f"frame_{self.now}", frame_index=self.now // 50, timestamp_ms=self.now + 5,
            shared_memory=self.memory,
        ))
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
        self.assertEqual(status["last_sample"], {"row": round(61 / BINS, 4), "ms": 150, "at_ms": car.now})
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
        self.assertEqual((status["run_ms"], status["runs"], status["uncounted_runs"]), (165, 2, 0))

    def test_runs_ended_any_other_way_are_not_counted(self):
        for name, ending in {
            "turn toward reach": [dict(maneuver={"phase": "turning", "cause": "reach", "direction": "left"})],
            "drive backwards": [dict(static=True, unchanged_ms=40), dict()],
            "reverse for another reason": [dict(maneuver={"phase": "backing_up"}, stop_reason="drive_backwards")],
        }.items():
            with self.subTest(name=name):
                car = _Car()
                for _ in range(4):
                    car.step()
                for step in ending:
                    car.step(**step)
                status = car.step(maneuver={"phase": "backing_up", "static_cycles": 0})
                self.assertEqual((status["run_ms"], status["runs"], status["uncounted_runs"]), (None, 0, 1))

    def test_rows_learned_at_another_resolution_are_dropped_and_reset_clears(self):
        car = _Car()
        for row in (60, 61, 62):
            car.step((1, row))
        car.step(maneuver={"phase": "backing_up", "static_cycles": 0})
        coarse = ApproachModel(row_bins=60)
        status = coarse.status(car.memory)
        self.assertEqual((status["rows"], status["row_bins"], status["runs"]), ([], 60, 1))
        write_observation(car.memory, None)
        coarse.update(DecisionFrameContext("coarse", 10, car.now + 100, shared_memory=car.memory))
        self.assertIsNone(read_belief(car.memory, "approach_model.ms_per_row"))
        coarse.reset(car.memory)
        self.assertNotIn(STATE_KEY, car.memory)
        self.assertEqual(ApproachModel().status(car.memory)["health"], "empty")
        ApproachModel().reset(None)

    def test_a_prediction_of_run_duration_is_checked_after_the_sample_window_fills(self):
        car = _Car(ApproachModel(max_samples=1))
        for _ in range(4):
            car.step()
        car.step(maneuver={"phase": "backing_up"})
        self.assertIsNone(read_belief(car.memory, "approach_model.run_ms").confidence)
        for _ in range(5):
            car.step()
        trial = read_belief(car.memory, "approach_model.run_ms").trials[0]
        car.step(maneuver={"phase": "backing_up"})
        belief = read_belief(car.memory, "approach_model.run_ms")
        self.assertEqual(len(belief.checks), 1)
        self.assertEqual(belief.checks[0].predicted, trial.predicted)
        self.assertEqual(belief.trials, ())

    def test_a_run_that_is_not_counted_withdraws_its_prediction(self):
        car = _Car()
        for _ in range(4):
            car.step()
        car.step(maneuver={"phase": "backing_up", "static_cycles": 0})
        car.step(maneuver={"phase": "turning"})
        for _ in range(3):
            car.step()
        self.assertEqual(len(read_belief(car.memory, "approach_model.run_ms").trials), 1)
        status = car.step(maneuver={"phase": "turning"})
        belief = read_belief(car.memory, "approach_model.run_ms")
        self.assertEqual((belief.trials, belief.withdrawals), ((), {"run_duration": {"run_not_counted": 1}}))
        self.assertEqual(status["uncounted_runs"], 1)

    def test_arrival_checks_use_the_original_row_prediction_as_samples_change(self):
        car = _Car()
        for row in (60, 61, 62):
            car.step((1, row))
        car.memory[belief_key("stop_row.row")] = Belief("stop_row.row", 62 / BINS, "stop_row", car.now)
        car.step((1, 60))
        car.step((1, 61))
        belief = read_belief(car.memory, "approach_model.ms_per_row")
        self.assertEqual(belief.trials[0].predicted, [50, 50])
        car.step((1, 62))
        belief = read_belief(car.memory, belief.belief_id)
        self.assertEqual([check.outcome for check in belief.checks], ["support"])
        self.assertEqual(belief.confidence, 2 / 3)
        self.assertEqual(belief.trials, ())

    def test_a_manual_span_or_capture_gap_retires_an_arrival_prediction(self):
        for gap in (50, 300):
            with self.subTest(gap=gap):
                car = _Car()
                for row in (60, 61, 62):
                    car.step((1, row))
                car.memory[belief_key("stop_row.row")] = Belief("stop_row.row", 62 / BINS, "stop_row", car.now)
                car.step((1, 60))
                car.step((1, 61))
                if gap == 50:
                    car.memory[CONTROL_APPLICATIONS_KEY] = (*car.memory[CONTROL_APPLICATIONS_KEY],
                        ControlApplication(None, "manual", False, "manual", AutonomyControl(), car.now + 1))
                car.step((1, 62), after=gap)
                belief = read_belief(car.memory, "approach_model.ms_per_row")
                self.assertEqual((belief.trials, belief.checks), ((), ()))
                self.assertEqual(belief.withdrawals, {"arrival": {"not_straight": 1}})

    def test_a_lost_edge_or_another_row_resolution_retires_an_arrival_prediction(self):
        def predicting():
            car = _Car()
            for row in (60, 61, 62):
                car.step((1, row))
            car.memory[belief_key("stop_row.row")] = Belief("stop_row.row", 62 / BINS, "stop_row", car.now)
            car.step((1, 60))
            car.step((1, 61))
            return car

        car = predicting()
        car.step((1, 61), (2, 30))
        car.step((2, 31))
        belief = read_belief(car.memory, "approach_model.ms_per_row")
        self.assertEqual((belief.trials, belief.withdrawals), ((), {"arrival": {"edge_lost": 1}}))
        car = predicting()
        car.step((1, 62))
        car.step((1, 60))
        car.step((1, 61))
        belief = read_belief(car.memory, "approach_model.ms_per_row")
        self.assertEqual((len(belief.checks), len(belief.trials)), (1, 1))
        car.memory[belief_key(belief.belief_id)] = replace(belief, properties={**belief.properties, "row_bins": 60})
        car.step((1, 62))
        belief = read_belief(car.memory, belief.belief_id)
        self.assertEqual((belief.checks, belief.properties["row_bins"]), ((), BINS))
        self.assertEqual((belief.withdrawals, belief.discards),
                         ({"arrival": {"row_bins_changed": 1}}, {"arrival": {"row_bins_changed": 1}}))

    def test_shared_memory_is_required(self):
        with self.assertRaises(ValueError):
            ApproachModel().update(DecisionFrameContext(
                frame_id="frame_1", frame_index=1, timestamp_ms=1, shared_memory=None,
            ))

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"edge_plugin_ids": []}, {"edge_plugin_ids": "sim_wall"}, {"edge_plugin_ids": [None]},
            {"sensor_id": ""}, {"stop_reasons": []}, {"stop_reasons": "stop"}, {"row_bins": 0},
            {"row_bins": True}, {"max_samples": 2.5}, {"max_interval_ms": -1}, {"min_x": -0.1},
            {"max_x": 1.2}, {"min_x": 0.6, "max_x": 0.4}, {"edge_margin": 0.6}, {"edge_margin": float("inf")},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                ApproachModel(**config)

    def test_arrival_prediction_reads_the_configured_stop_belief_and_ignores_nonnumeric_values(self):
        for value, expected in ((63 / BINS, True), (None, False), (True, False), (".5", False)):
            with self.subTest(value=value):
                car = _Car(ApproachModel(stop_row_belief_id="custom.stop"))
                car.memory[belief_key("custom.stop")] = Belief("custom.stop", value, "stop_row", 1)
                for row in (60, 61, 62, 63, 60, 61):
                    car.step((1, row))
                belief = read_belief(car.memory, "approach_model.ms_per_row")
                self.assertEqual(bool(belief.trials), expected)
        for value in (None, "", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ApproachModel(stop_row_belief_id=value)
