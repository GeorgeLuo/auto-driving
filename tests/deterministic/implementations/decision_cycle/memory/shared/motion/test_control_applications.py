"""Learners use covered application intervals, including commands between captures."""
import unittest
from dataclasses import replace

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from implementations.decision_cycle.memory.shared.motion.control_applications import motion_at, motions_over


def _application(at, steering=0, throttle=.6, mode="autonomy"):
    return ControlApplication(None, mode, True, "test", AutonomyControl(steering, throttle), at)


class ControlIntervalTests(unittest.TestCase):
    def test_the_command_held_at_capture_and_every_intervening_write_count(self):
        memory = {CONTROL_APPLICATIONS_KEY: (
            _application(10), _application(25, 1), _application(30), _application(40, -1, -.6),
        )}
        self.assertEqual(motions_over(memory, 20, 35), ("forward", "forward_right", "forward"))
        self.assertEqual(motion_at(memory, 40), "reverse_left")

    def test_equal_time_writes_use_the_last_target_at_the_interval_start(self):
        memory = {CONTROL_APPLICATIONS_KEY: (_application(10), _application(10, -1), _application(20, 1))}
        self.assertEqual(motions_over(memory, 10, 19), ("forward_left",))
        self.assertEqual(motions_over(memory, 10, 20), ("forward_left", "forward_right"))

    def test_missing_coverage_manual_modes_and_clock_steps_are_unknown(self):
        application = _application(10)
        for applications, start, end in (
            (None, 10, 20), ((), 10, 20), ((application,), 5, 20),
            ((application,), 20, 10), ((application, _application(5)), 10, 20),
            ((application, _application(15, mode="manual"), _application(19)), 10, 20),
            ((application, _application(15, mode="observe_only")), 10, 20),
            ((replace(application, applied=False),), 10, 20),
        ):
            with self.subTest(applications=applications, start=start, end=end):
                memory = {} if applications is None else {CONTROL_APPLICATIONS_KEY: applications}
                self.assertIsNone(motions_over(memory, start, end))

    def test_idle_and_reverse_do_not_count_as_forward(self):
        memory = {CONTROL_APPLICATIONS_KEY: (_application(10, throttle=0), _application(20, throttle=-.6))}
        self.assertEqual(motions_over(memory, 10, 20), ("idle", "reverse"))
