from dataclasses import replace
import unittest

from autonomy.decision_cycle.action.publication import (
    CONTROL_APPLICATIONS_KEY, control_applications_over, read_control_applications,
)
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication


def application(at, **changes):
    return replace(ControlApplication(None, "autonomy", True, "drive", AutonomyControl(throttle=0.3), at), **changes)


class ControlApplicationReaderTests(unittest.TestCase):
    def test_reader_accepts_tuples_and_decoded_lists(self):
        record = application(10)
        for value in ((record,), [record], ()):
            self.assertEqual(read_control_applications({CONTROL_APPLICATIONS_KEY: value}), tuple(value))
        for shared in (None, {}, {CONTROL_APPLICATIONS_KEY: {}}, {CONTROL_APPLICATIONS_KEY: [record.to_dict()]}):
            self.assertIsNone(read_control_applications(shared))

    def test_interval_includes_starting_control_and_writes_through_end(self):
        records = [application(10), application(20), application(30), application(40)]
        shared = {CONTROL_APPLICATIONS_KEY: records}
        self.assertEqual(control_applications_over(shared, 15, 30), tuple(records[:3]))
        self.assertEqual(control_applications_over(shared, 20, 20), (records[1],))
        self.assertEqual(control_applications_over(shared, 40, 50), (records[-1],))
        self.assertIsNone(control_applications_over(shared, 0, 30))
        self.assertIsNone(control_applications_over(shared, 30, 20))
        self.assertIsNone(control_applications_over({}, 0, 30))
        self.assertIsNone(control_applications_over({CONTROL_APPLICATIONS_KEY: ()}, 0, 30))

    def test_equal_timestamps_preserve_write_order_and_last_write_at_start(self):
        records = [application(10), application(20), application(20, reason="turn"), application(30)]
        shared = {CONTROL_APPLICATIONS_KEY: records}
        self.assertEqual(control_applications_over(shared, 10, 20), tuple(records[:3]))
        self.assertEqual(control_applications_over(shared, 20, 30), tuple(records[2:]))

    def test_unknown_motion_in_the_span_or_backwards_clock_rejects_interval(self):
        for record in (application(20, mode="manual"), application(20, mode="observe_only"),
                       application(20, applied=False), application(None)):
            with self.subTest(record=record):
                shared = {CONTROL_APPLICATIONS_KEY: [application(10), record, application(30)]}
                self.assertIsNone(control_applications_over(shared, 15, 30))
        self.assertIsNone(control_applications_over({CONTROL_APPLICATIONS_KEY: [application(20), application(10)]}, 20, 30))
        shared = {CONTROL_APPLICATIONS_KEY: [application(10, mode="manual"), application(20), application(30, mode="manual")]}
        self.assertEqual(control_applications_over(shared, 20, 25), (shared[CONTROL_APPLICATIONS_KEY][1],))
