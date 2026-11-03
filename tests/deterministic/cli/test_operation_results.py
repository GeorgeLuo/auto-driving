"""Command output is a rendering of operation facts, without resource access."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from cli.automa_cli.vehicles.models import VehicleResult
from cli.automa_cli.vehicles.render import render_vehicle_result
from cli.automa_cli.simulators.models import SimulatorResult
from cli.automa_cli.simulators.render import render_simulator_result


class OperationResultTests(unittest.TestCase):
    def test_vehicle_removal_formats_supplied_records(self):
        result = VehicleResult(0, "removed", vehicles=({"provider": "picar", "url": "http://car"},))
        with patch("builtins.open", side_effect=AssertionError("renderer opened a file")):
            self.assertEqual(render_vehicle_result(result), "Removed PiCar (picar) at http://car")
        self.assertEqual(result.vehicles[0]["url"], "http://car")

    def test_simulator_blocking_hosts_and_cleanup_are_facts(self):
        url = "ws://localhost:5051/ws/control"
        with patch("socket.create_connection", side_effect=AssertionError("renderer connected")):
            for hosts, beginning, ending in [
                (("one",), "Host one uses", "delete it first."),
                (("one", "two"), "Hosts one, two use", "delete them first."),
            ]:
                result = SimulatorResult(2, "blocked", url=url, hosts=hosts)
                text = render_simulator_result(result)
                self.assertTrue(text.startswith(beginning), text)
                self.assertTrue(text.endswith(ending), text)
            removed = SimulatorResult(0, "deleted", url=url, killed=True)
            self.assertTrue(render_simulator_result(removed).endswith(", killed"))

    def test_not_found_preserves_available_records(self):
        result = VehicleResult(2, "missing", url="http://absent", vehicles=())
        self.assertEqual(render_vehicle_result(result), "No discovered vehicle at http://absent. None discovered.")
        simulator = SimulatorResult(2, "missing", url="http://absent")
        self.assertEqual(render_simulator_result(simulator), "No created simulator at http://absent. None created.")
