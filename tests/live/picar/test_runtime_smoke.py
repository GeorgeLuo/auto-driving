from __future__ import annotations

import os
import unittest

from cli.automa_cli.deploy import inspect_physical_autonomy_runtime
from implementations.vehicle.picar.defaults import DEFAULT_LOCAL_CAR_BASE_URL


@unittest.skipUnless(
    os.environ.get("AUTOMA_TEST_LIVE_PI") == "1",
    "run tests/run.py --live-pi to enable the read-only Pi smoke test",
)
class PiRuntimeSmokeTests(unittest.TestCase):
    def test_runtime_is_reachable_activated_and_manual(self) -> None:
        status = inspect_physical_autonomy_runtime(
            base_url=os.environ.get("AUTOMA_TEST_PICAR_URL", DEFAULT_LOCAL_CAR_BASE_URL),
            timeout_s=float(os.environ.get("AUTOMA_TEST_PICAR_TIMEOUT_S", "3.0")),
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["drive_mode"], "user")
        self.assertTrue(status["engine"])
        self.assertTrue(status["perception_algorithm"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
