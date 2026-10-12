from __future__ import annotations

import unittest

from autonomy.vehicle import CarInterface


class VehicleInterfaceTests(unittest.TestCase):
    def test_vehicle_contract_has_one_sensor_read_path(self) -> None:
        self.assertTrue(hasattr(CarInterface, "read_sensors"))
        self.assertFalse(hasattr(CarInterface, "capture_frame"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
