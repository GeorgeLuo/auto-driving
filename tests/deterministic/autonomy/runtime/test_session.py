"""Run-configuration serialization and request decoding."""
from __future__ import annotations

import unittest

from autonomy.runtime.session import RunConfiguration


class RunConfigurationTests(unittest.TestCase):
    def test_to_dict_round_trips_through_request_decoding(self) -> None:
        configuration = RunConfiguration(mode="observe_only", num_decisions=2)
        self.assertEqual(RunConfiguration.from_dict(configuration.to_dict()), configuration)

    def test_request_decoding_requires_every_setting(self) -> None:
        for data in ({}, {"mode": "autonomy"}):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, "num_decisions"):
                    RunConfiguration.from_dict(data)

    def test_request_decoding_requires_an_object(self) -> None:
        with self.assertRaisesRegex(ValueError, "configuration must be an object"):
            RunConfiguration.from_dict(None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
