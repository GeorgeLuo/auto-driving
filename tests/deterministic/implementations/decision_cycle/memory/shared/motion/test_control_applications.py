"""Learners use covered application intervals, including commands between captures."""
import unittest
from dataclasses import replace

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from implementations.decision_cycle.memory.shared.motion.control_applications import application_at, latest_motion, motion_at, motions_over


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

    def test_document_round_trip_keeps_motion_and_latest_issued_target(self):
        from autonomy.shared_memory import shared_memory_document, shared_memory_from_document
        memory = {CONTROL_APPLICATIONS_KEY: (_application(10), _application(25, throttle=-.6))}
        restored = shared_memory_from_document(shared_memory_document(memory))
        self.assertIsInstance(restored[CONTROL_APPLICATIONS_KEY], list)
        self.assertEqual(motions_over(restored, 20, 30), ("forward", "reverse"))
        self.assertEqual(motion_at(restored, 20), "forward")
        self.assertEqual(latest_motion(restored), "reverse")
        self.assertEqual(application_at(restored, 30).control.throttle, -.6)

    def test_latest_target_does_not_skip_an_unknown_or_manual_write(self):
        for last in (_application(20, mode="manual"), replace(_application(20), applied=False),
                     replace(_application(20), delivered_at_ms=None), _application(5)):
            with self.subTest(last=last):
                self.assertIsNone(latest_motion({CONTROL_APPLICATIONS_KEY: (_application(10), last)}))
