"""The stop row is a hypothesis: a collision starts it, and each stop narrows it by bisection."""

import unittest
from dataclasses import replace

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.decision_cycle.memory.publication import belief_key, read_belief
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication


from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.memory.plugins.stop_row.plugin import StopRowHypothesis



class _Car:
    """Feeds one observation per 100 ms cycle to a ``StopRowHypothesis``."""

    def __init__(self, plugin=None, memory=None):
        self.plugin = plugin or StopRowHypothesis()
        self.memory = {} if memory is None else memory
        self.now = 0

    def step(self, row=None, *, static=None, wall=True, phase=None, observed=True):
        self.now += 100
        command = AutonomyControl(steering=1 if phase == "turning" else 0,
            throttle=.6 if phase is None or phase == "turning" else -.6)
        applications = self.memory.get(CONTROL_APPLICATIONS_KEY, ())
        self.memory[CONTROL_APPLICATIONS_KEY] = (*applications, ControlApplication(
            None, "autonomy", True, "test", command, self.now - 1))[-32:]
        observation = Observation(
            observation_id=f"observation_{self.now}", created_at_ms=self.now, sensor_frame={},
            signals=(
                {"signal_id": "view_static", "value": static, "confidence": None, "properties": {}},
                {"signal_id": "wall_in_sight", "value": wall, "confidence": None, "properties": {}},
                {"signal_id": "clearance_ahead", "value": True, "confidence": None,
                 "properties": {"nearest_bottom_y": row}},
            ),
        ) if observed else None
        self.plugin.update(DecisionFrameContext(
            frame_id=f"frame_{self.now}", frame_index=self.now // 100, timestamp_ms=self.now,
            shared_memory=self.memory,
        ), observation)
        return self.plugin.status(self.memory)

    def collide(self, row=1.0):
        return self.step(row, static=True)

    def stop(self, peak, cycles=11):
        """Reach the stop row, then turn away while the lower edge peaks at ``peak``."""

        self.step(read_belief(self.memory, "stop_row.row").value)
        for _ in range(cycles):
            status = self.step(peak, phase="backing_up")
        return status


class StopRowHypothesisTests(unittest.TestCase):
    def test_driving_without_a_collision_learns_nothing(self):
        car = _Car()
        self.assertEqual(car.plugin.status(car.memory)["health"], "empty")
        for row in (None, 0.5, 0.9, 1.0):
            status = car.step(row)
        self.assertIsNone(status["stop_row"])
        self.assertEqual(status["record_count"], 0)
        self.assertFalse(status["testing"])

    def test_the_first_collision_starts_halfway_between_the_bounds(self):
        car = _Car()
        car.step(0.7)
        status = car.collide()
        self.assertEqual((status["stop_row"], status["safe_row"], status["unsafe_row"]), (0.79, 0.6, 0.98))
        self.assertEqual(status["counts"], {"collision": 1, "stopped_short": 0, "too_close": 0})
        self.assertEqual(status["last_outcome"], {"kind": "collision", "stop_row": None, "peak_row": 1.0, "at_ms": 200})

    def test_a_static_view_while_maneuvering_or_with_nothing_in_sight_is_not_a_collision(self):
        car = _Car()
        car.step(1.0, static=True, phase="backing_up")
        status = car.step(1.0, static=True, wall=False)
        self.assertIsNone(status["stop_row"])
        self.assertEqual(status["record_count"], 0)

    def test_stopping_short_moves_the_stop_row_deeper(self):
        car = _Car()
        car.collide()
        status = car.step(0.8)
        self.assertTrue(status["testing"])
        for _ in range(9):
            status = car.step(0.9, phase="backing_up")
        self.assertTrue(status["testing"])  # settle_ms has not passed
        status = car.step(0.9, phase="backing_up")
        self.assertFalse(status["testing"])
        self.assertEqual(status["last_outcome"], {"kind": "stopped_short", "stop_row": 0.79, "peak_row": 0.9, "at_ms": 1200})
        self.assertEqual((status["safe_row"], status["unsafe_row"], status["stop_row"]), (0.79, 0.98, 0.885))

    def test_coming_too_close_moves_the_stop_row_earlier_at_once(self):
        car = _Car()
        car.collide()
        car.step(0.79)
        status = car.step(0.98, phase="backing_up")
        self.assertEqual(status["last_outcome"]["kind"], "too_close")
        self.assertEqual((status["safe_row"], status["unsafe_row"], status["stop_row"]), (0.6, 0.79, 0.695))

    def test_bisection_settles_on_the_deepest_row_that_stops_short(self):
        car = _Car()
        car.collide()
        overshoot = 0.12  # contact once the trigger row is above 0.86
        for _ in range(8):
            stop_row = read_belief(car.memory, "stop_row.row").value
            status = car.stop(min(1.0, stop_row + overshoot))
        self.assertLess(status["unsafe_row"] - status["safe_row"], 0.02)
        self.assertEqual(status["stop_row"], status["safe_row"])
        self.assertLessEqual(status["stop_row"] + overshoot, 0.98)
        self.assertGreater(status["stop_row"], 0.84)
        self.assertEqual(status["last_outcome"]["kind"], "stopped_short")

    def test_a_collision_without_a_test_is_counted_but_does_not_move_the_stop_row(self):
        car = _Car()
        car.collide()
        status = car.collide(0.57)  # the stop row was never reached, so the obstacle was not ahead
        self.assertEqual(status["last_outcome"], {"kind": "collision", "stop_row": 0.79, "peak_row": 0.57, "at_ms": 200})
        self.assertEqual((status["safe_row"], status["unsafe_row"], status["stop_row"]), (0.6, 0.98, 0.79))
        self.assertEqual(car.collide(None)["counts"]["collision"], 3)

    def test_the_test_ends_when_the_stop_ends_and_ignores_rows_seen_while_turning(self):
        car = _Car()
        car.collide()
        car.step(0.79)
        car.step(0.82, phase="backing_up")
        status = car.step(1.0, phase="turning")  # a turn swept another box in
        self.assertEqual(status["last_outcome"], {"kind": "stopped_short", "stop_row": 0.79, "peak_row": 0.82, "at_ms": 400})
        self.assertEqual(status["stop_row"], 0.885)
        self.assertFalse(car.step(1.0, phase="turning")["testing"])

    def test_a_test_with_no_stop_after_it_is_dropped(self):
        car = _Car()
        car.collide()
        car.step(0.8)
        status = car.step(0.99)
        self.assertFalse(status["testing"])
        self.assertEqual(status["counts"], {"collision": 1, "stopped_short": 0, "too_close": 0})
        self.assertEqual(status["stop_row"], 0.79)

    def test_contradicting_evidence_replaces_the_older_bound(self):
        car = _Car()
        car.collide()
        car.stop(0.9)  # 0.79 stops short
        car.memory[belief_key("stop_row.row")] = replace(read_belief(car.memory, "stop_row.row"), value=0.75)
        status = car.stop(0.98)  # 0.75 now comes too close, below the safe row
        self.assertEqual((status["safe_row"], status["unsafe_row"]), (0.6, 0.75))
        car.memory[belief_key("stop_row.row")] = replace(read_belief(car.memory, "stop_row.row"), value=0.8)
        status = car.stop(0.9)  # 0.8 stops short, above the unsafe row
        self.assertEqual((status["safe_row"], status["unsafe_row"], status["stop_row"]), (0.8, 0.98, 0.89))

    def test_rows_below_the_stop_row_or_a_missing_observation_do_not_start_a_test(self):
        car = _Car()
        car.collide()
        for kwargs in ({"row": 0.78}, {"row": None}, {"observed": False}, {"row": 0.9, "phase": "turning"}):
            with self.subTest(**kwargs):
                self.assertFalse(car.step(**kwargs)["testing"])

    def test_a_test_ends_on_time_without_observations(self):
        car = _Car()
        car.collide()
        car.step(0.8)
        for _ in range(10):
            status = car.step(observed=False, phase="backing_up")
        self.assertEqual(status["last_outcome"]["kind"], "stopped_short")
        self.assertEqual(status["last_outcome"]["peak_row"], 0.8)

    def test_the_hypothesis_continues_across_fresh_instances_and_reset_clears_it(self):
        memory = {"unrelated": 1}
        car = _Car(memory=memory)
        car.collide()
        self.assertEqual(_Car(memory=memory).step(0.5)["stop_row"], 0.79)
        StopRowHypothesis().reset(memory)
        self.assertEqual({k: v for k, v in memory.items() if k != CONTROL_APPLICATIONS_KEY}, {"unrelated": 1})
        status = StopRowHypothesis().status(memory)
        self.assertEqual((status["health"], status["record_count"], status["stop_row"]), ("empty", 0, None))
        StopRowHypothesis().reset(None)

    def test_configured_bounds_and_settle_time_are_used(self):
        car = _Car(StopRowHypothesis(min_stop_row=0.7, blind_row=0.9, settle_ms=200))
        status = car.collide()
        self.assertEqual(status["stop_row"], 0.8)
        car.step(0.8)
        status = car.step(0.85, phase="reversing")
        self.assertTrue(status["testing"])
        status = car.step(0.85, phase="reversing")
        self.assertEqual(status["last_outcome"]["kind"], "stopped_short")

    def test_checks_count_stop_outcomes_and_bracket_invalidation_retires_old_checks(self):
        car = _Car()
        car.collide()
        self.assertIsNone(read_belief(car.memory, "stop_row.row").confidence)
        car.stop(.9)
        belief = read_belief(car.memory, "stop_row.row")
        self.assertEqual((belief.confidence, len(belief.checks), belief.trials), (2 / 3, 1, ()))
        car.memory[belief_key(belief.belief_id)] = replace(belief, value=.75)
        car.stop(.98)
        belief = read_belief(car.memory, "stop_row.row")
        self.assertEqual(belief.checks, ())
        self.assertIsNone(belief.confidence)

    def test_missing_or_manual_motion_cannot_seed_a_collision_or_complete_a_stop(self):
        car = _Car()
        car.collide()
        car.step(.8)
        car.memory[CONTROL_APPLICATIONS_KEY] = (*car.memory[CONTROL_APPLICATIONS_KEY],
            ControlApplication(None, "manual", False, "manual", AutonomyControl(), car.now + 1))
        status = car.step(.9, phase="backing_up")
        self.assertFalse(status["testing"])
        self.assertEqual(read_belief(car.memory, "stop_row.row").checks, ())
        memory = {}
        StopRowHypothesis().update(DecisionFrameContext(
            frame_id="unknown", frame_index=0, timestamp_ms=10, shared_memory=memory),
            Observation(observation_id="o", created_at_ms=10, sensor_frame={}, signals=(
                {"signal_id": "view_static", "value": True}, {"signal_id": "wall_in_sight", "value": True})))
        self.assertIsNone(read_belief(memory, "stop_row.row"))

    def test_without_shared_memory_the_update_fails(self):
        with self.assertRaises(ValueError):
            StopRowHypothesis().update(DecisionFrameContext(frame_id="f", frame_index=0, timestamp_ms=1), None)

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"min_stop_row": 0}, {"min_stop_row": True}, {"blind_row": 1.5}, {"resolution": float("nan")},
            {"min_stop_row": 0.9, "blind_row": 0.8}, {"settle_ms": 0}, {"settle_ms": 1.5},
            {"obstacle_signal_ids": []}, {"obstacle_signal_ids": "wall_in_sight"},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                StopRowHypothesis(**config)
