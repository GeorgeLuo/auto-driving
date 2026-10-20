from __future__ import annotations

import unittest

from autonomy.decision_cycle.control_history import HeldCommand, commands_over


def delivery(at_ms, steering=0.0, throttle=0.5):
    return {"delivered_at_ms": at_ms, "steering": steering, "throttle": throttle}


class ControlHistoryTests(unittest.TestCase):
    def test_last_command_at_start_is_held_for_the_interval(self):
        history = (delivery(10, -0.5), delivery(20, 0.5))
        self.assertEqual(commands_over(history, 20, 50), (HeldCommand(0.5, 0.5, 20, 50),))

    def test_change_and_idle_stop_split_the_interval(self):
        history = (delivery(10), delivery(30, -0.5), delivery(40, 0.0, 0.0))
        self.assertEqual(commands_over(history, 20, 50), (
            HeldCommand(0.0, 0.5, 20, 30), HeldCommand(-0.5, 0.5, 30, 40),
            HeldCommand(0.0, 0.0, 40, 50),
        ))

    def test_history_must_reach_the_start(self):
        self.assertIsNone(commands_over((), 10, 20))
        self.assertIsNone(commands_over((delivery(11),), 10, 20))

    def test_same_millisecond_last_delivery_wins_and_end_is_exclusive(self):
        history = (delivery(10), delivery(10, -1.0), delivery(20, 1.0))
        self.assertEqual(commands_over(history, 10, 20), (HeldCommand(-1.0, 0.5, 10, 20),))
        self.assertEqual(commands_over(history, 10, 10), ())
        with self.assertRaises(ValueError):
            commands_over(history, 20, 10)
