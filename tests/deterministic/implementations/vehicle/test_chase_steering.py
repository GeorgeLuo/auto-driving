from __future__ import annotations

import unittest
from unittest import mock

from autonomy.vehicle import VehicleAction
from implementations.vehicle.chase_sim import ChaseSimCar


class ChaseSteeringSignTests(unittest.TestCase):
    def test_a_right_turn_is_sent_as_chases_negative_steering(self) -> None:
        """The engine steers right on +1 and Chase left, so the adapter flips the sign it sends."""

        car = ChaseSimCar(ws_url="ws://example.test/ws")
        with mock.patch.object(car.client, "play_game_command", return_value={}) as command:
            car.execute_action(VehicleAction(forward=True, steering=0.5), throttle=1.0)
            car.execute_action(VehicleAction(reverse=True, steering=-1.0), throttle=1.0)
            car.execute_action(VehicleAction(), throttle=0.0)
        sent = [call.args[1]["steering"] for call in command.call_args_list]
        self.assertEqual(sent, [-0.5, 1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
