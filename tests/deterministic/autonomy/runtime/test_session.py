"""Run-configuration serialization and request decoding."""
from __future__ import annotations

import unittest

from autonomy.runtime.session import RunConfiguration


class RunConfigurationTests(unittest.TestCase):
    def test_to_dict_round_trips_through_request_decoding(self) -> None:
        configuration = RunConfiguration(mode="observe_only", num_decisions=2)
        self.assertEqual(RunConfiguration.from_dict(configuration.to_dict()), configuration)

    def test_request_decoding_requires_every_setting(self) -> None:
        # A missing mode would otherwise default to autonomy, which takes control.
        for data, reason in (
            (None, "configuration must name mode, num_decisions"),
            ({}, "configuration must name mode, num_decisions"),
            ({"mode": "autonomy"}, "configuration must name num_decisions"),
            ({"num_decisions": 3}, "configuration must name mode"),
        ):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, f"^{reason}$"):
                    RunConfiguration.from_dict(data)

    def test_request_decoding_refuses_other_shapes(self) -> None:
        for data, reason in (
            ([], "configuration must be an object"),
            ({"mode": "autonomy", "num_decisions": 2, "interval_s": 1},
             "configuration has unknown keys interval_s"),
        ):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, f"^{reason}$"):
                    RunConfiguration.from_dict(data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
