"""Action-time integration and escape behavior at the memory step boundary."""

import json
import math
import unittest

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.publication import write_observation
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import PerceptionSignal
from autonomy.decision_cycle.steps import STEP_RUNNERS
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from autonomy.shared_memory import shared_memory_document, shared_memory_from_document
from implementations.decision_cycle.catalog import selection_activation
from implementations.decision_cycle.memory.plugins.relativistic_trail.plugin import (
    CELL_LIMIT,
    STATE_KEY,
    TURN_AWAY_TIMEOUT_MS,
    RelativisticTrail,
)
from implementations.decision_cycle.memory.plugins.relativistic_trail.trail import (
    TRAIL_KEY,
    VISIT_HALF_LIFE_MS,
    decayed_weight,
    read_relativistic_trail,
)


class RelativisticTrailTests(unittest.TestCase):
    def setUp(self):
        self.memory = {}
        self.runner = STEP_RUNNERS["memory"].from_activation(
            selection_activation("memory", plugins=["relativistic_trail"])
        )
        self.apply(900)
        self.update(1000)

    def apply(self, at, throttle=0.6, steering=0.0, mode="autonomy"):
        previous = self.memory.get(CONTROL_APPLICATIONS_KEY, ())
        application = ControlApplication(
            f"at-{at}", mode, True, "test",
            AutonomyControl(steering=steering, throttle=throttle), at,
        )
        self.memory[CONTROL_APPLICATIONS_KEY] = (*previous[-31:], application)

    def update(self, at, *, yaw=None, direction="unknown", blocked=False, impeded=False, fit=None, sign="normal",
               yaw_properties=None):
        signals = [
            PerceptionSignal(
                "visual_yaw_rate_rad_s", None, properties={
                    "confident": yaw is not None, "yaw_delta_rad": yaw,
                    "motion_blocked": blocked, "motion_impeded": impeded,
                    **(yaw_properties or {}),
                }, source_plugin_id="vehicle_dynamics",
            ),
            PerceptionSignal(
                "visual_expansion_rate_per_s", None, properties={"motion_direction": direction, "throttle_sign": sign},
                source_plugin_id="vehicle_dynamics",
            ),
        ]
        if fit is not None:
            signals.append(PerceptionSignal(
                "turn_response_available", True, properties=fit, source_plugin_id="vehicle_dynamics",
            ))
        write_observation(self.memory, Observation(f"f-{at}", at, {}, signals=tuple(signals)))
        self.runner.update(DecisionFrameContext(f"f-{at}", at, at, shared_memory=self.memory))
        return read_relativistic_trail(self.memory)

    def test_integrates_midpoint_and_bridges_abstention_with_the_applied_intervals(self):
        fit = {"kind": "ackermann_tan", "gain_per_s": 3.0, "max_angle_rad": 0.7, "turn_sign": "normal"}
        trail = self.update(1200, yaw=0.4, direction="forward", fit=fit)
        self.assertAlmostEqual(trail["pose"]["x_s"], 0.2 * math.sin(0.2))
        self.assertAlmostEqual(trail["pose"]["z_s"], 0.2 * math.cos(0.2))

        self.apply(1200, throttle=0.6, steering=0.50)
        self.apply(1300, throttle=0.6, steering=0.75)
        trail = self.update(1400)
        predicted = 0.1 * 3.0 * 0.6 * (math.tan(0.7 * 0.50) + math.tan(0.7 * 0.75))
        self.assertAlmostEqual(trail["pose"]["heading_rad"], 0.4 + predicted)
        self.assertAlmostEqual(trail["pose"]["x_s"], 0.2 * (math.sin(0.2) + math.sin(0.4 + predicted / 2)))
        self.assertEqual(trail["applied_steering"], 0.75)

        self.apply(1400, throttle=-0.6, steering=0.0)
        trail = self.update(1600, yaw=0.0, sign="inverted")
        self.assertAlmostEqual(
            trail["pose"]["z_s"], 0.2 * (math.cos(0.2) + math.cos(0.4 + predicted / 2) + math.cos(0.4 + predicted))
        )

    def test_blocked_and_impeded_motion_cannot_accumulate_full_travel(self):
        self.update(1200, yaw=0.0, direction="forward", fit={
            "kind": "linear", "gain_per_s": 4.0, "max_angle_rad": 0.0, "turn_sign": "normal",
        })
        self.apply(1200, steering=0.8)
        blocked = self.update(1400, blocked=True, direction="forward")
        self.assertEqual(blocked["pose"], {"x_s": 0.0, "z_s": 0.2, "heading_rad": 0.0})
        impeded = self.update(1600, impeded=True, direction="forward")
        self.assertAlmostEqual(impeded["pose"]["z_s"], 0.25)
        self.assertEqual(impeded["pose"]["heading_rad"], 0.0)
        self.assertEqual(len(impeded["contacts"]), 1)
        self.assertEqual(impeded["contacts"][0]["at_ms"], 1600)

    def test_visual_stationarity_and_uncovered_or_non_autonomous_abstentions(self):
        self.assertEqual(self.update(1200, direction="stationary")["pose"]["z_s"], 0.0)
        self.apply(1200, mode="manual")
        self.assertEqual(self.update(1400)["pose"]["z_s"], 0.0)
        self.apply(1500)
        self.assertEqual(self.update(2400)["pose"]["z_s"], 0.0)

    def test_long_gap_integrates_control_changes_without_using_the_camera_pair(self):
        fit = {"kind": "linear", "gain_per_s": 4.0, "max_angle_rad": 0.0, "turn_sign": "normal"}
        self.update(1200, yaw=0.0, direction="stationary", fit=fit)
        self.apply(1200, steering=0.50)
        self.apply(1500, steering=-0.25)
        trail = self.update(2400, yaw=2.0, direction="stationary", blocked=True)
        self.assertAlmostEqual(trail["interval_s"], 1.2)
        self.assertAlmostEqual(trail["pose"]["heading_rad"], (0.3 * 0.50 - 0.9 * 0.25) * 4.0 * 0.6)
        self.assertGreater(trail["pose"]["z_s"], 1.1)
        self.assertEqual(trail["applied_steering"], -0.25)
        self.assertEqual(trail["contacts"], [])
        self.assertEqual(self.update(2400), trail)

    def test_turn_expansion_cannot_reverse_travel_for_known_throttle_polarity(self):
        self.apply(1000, steering=0.50)
        trail = self.update(1200, yaw=0.4, direction="reverse", sign="normal")
        self.assertGreater(trail["pose"]["z_s"], 0.0)
        self.assertAlmostEqual(trail["pose"]["heading_rad"], 0.4)

    def test_sustained_visual_stationarity_under_throttle_initiates_escape(self):
        trail = self.update(1200, yaw=0.0, direction="stationary")
        self.assertIsNone(trail["escape"])
        trail = self.update(1400, yaw=0.0, direction="stationary")
        self.assertEqual(trail["pose"]["z_s"], 0.0)
        self.assertEqual(trail["escape"]["phase"], "back_out")

    def test_sparse_stationary_tracks_trigger_escape_but_missing_tracks_bridge(self):
        yaw_properties = {"mode": "abstain_low_tracks", "num_tracks": 0, "median_px_shift": 0.0}
        self.assertAlmostEqual(self.update(1200, yaw_properties=yaw_properties)["pose"]["z_s"], 0.2)
        yaw_properties["num_tracks"] = 2
        self.assertIsNone(self.update(1400, yaw_properties=yaw_properties)["escape"])
        trail = self.update(1600, yaw_properties=yaw_properties)
        self.assertEqual(trail["pose"]["z_s"], 0.2)
        self.assertEqual(trail["escape"]["phase"], "back_out")

    def test_continued_tracking_loss_triggers_bounded_recovery_without_recording_contact(self):
        for at in range(1250, 3000, 250):
            trail = self.update(at)
        self.assertEqual(trail["escape"]["cause"], "tracking")
        self.assertEqual(trail["contacts"], [])

    def test_duplicate_capture_is_ignored_and_backwards_clock_starts_new_origin(self):
        trail = self.update(1200, yaw=0.5, direction="forward")
        self.assertEqual(self.update(1200, yaw=0.5, direction="forward"), trail)
        restarted = self.update(1100, yaw=0.5, direction="forward")
        self.assertEqual(restarted["pose"], {"x_s": 0.0, "z_s": 0.0, "heading_rad": 0.0})

    def test_round_trip_keeps_escape_state_and_detaches_the_public_reader(self):
        self.update(1200, blocked=True)
        saved = shared_memory_document(self.memory)
        restored = shared_memory_from_document(json.loads(json.dumps(saved)))
        self.assertEqual(shared_memory_document(restored), saved)
        detached = read_relativistic_trail(restored)
        detached["contacts"][0]["at_ms"] = 9999
        self.assertEqual(read_relativistic_trail(restored)["contacts"][0]["at_ms"], 1200)
        self.memory = restored
        self.apply(1200, throttle=-0.6)
        self.assertEqual(self.update(1400)["escape"]["backing_s"], 0.2)
        self.runner.reset(self.memory)
        self.assertNotIn(TRAIL_KEY, self.memory)
        self.assertNotIn(STATE_KEY, self.memory)
        self.assertIn(CONTROL_APPLICATIONS_KEY, self.memory)
        self.assertEqual(RelativisticTrail().status(self.memory), {"available": False})
        self.assertEqual(self.update(1600)["pose"]["z_s"], 0.0)

    def test_escape_counts_backing_then_turns_until_relative_heading_reaches_target(self):
        start = self.update(1200, yaw=0.35, blocked=True)["escape"]
        self.assertEqual((start["phase"], start["direction"]), ("back_out", -1))
        self.apply(1200, throttle=-0.60)
        self.assertEqual(self.update(1450)["escape"]["phase"], "back_out")
        turning = self.update(1700)["escape"]
        self.assertEqual((turning["phase"], turning["direction"]), ("turn_away", 1))
        self.apply(1700)
        self.assertEqual(self.update(1900, yaw=0.80, direction="forward")["escape"]["phase"], "turn_away")
        self.assertIsNone(self.update(2100, yaw=0.78, direction="forward")["escape"])

    def test_backing_into_contact_advances_escape_and_turn_timeout_is_bounded(self):
        self.update(1200, blocked=True)
        self.apply(1200, throttle=-0.60)
        trail = self.update(1300, impeded=True)
        self.assertEqual(trail["escape"]["phase"], "turn_away")
        self.assertEqual(trail["escape"]["direction"], 1)
        self.assertEqual(trail["contacts"][-1]["direction"], -1)
        self.apply(1300)
        self.assertEqual(self.update(1400, blocked=True)["escape"]["direction"], -1)
        self.assertIsNone(self.update(1300 + TURN_AWAY_TIMEOUT_MS)["escape"])

    def test_tight_spin_initiates_loop_escape(self):
        for at in range(1200, 2600, 200):
            trail = self.update(at, yaw=0.65, direction="stationary")
        self.assertEqual(trail["escape"]["cause"], "loop")
        self.assertEqual(trail["escape"]["phase"], "back_out")

    def test_cells_remain_bounded_and_old_visits_decay(self):
        for index in range(1, CELL_LIMIT + 12):
            trail = self.update(1000 + index * 450, yaw=0.0, direction="forward")
        self.assertEqual(len(trail["visited"]), CELL_LIMIT)
        self.assertNotIn("0,0", trail["visited"])
        cell = max(trail["visited"].values(), key=lambda old: old["last_visited_at_ms"])
        self.assertAlmostEqual(decayed_weight(cell, trail["at_ms"] + VISIT_HALF_LIFE_MS), cell["weight"] / 2)
