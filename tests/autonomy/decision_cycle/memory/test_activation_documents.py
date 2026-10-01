from __future__ import annotations

import tempfile
import unittest

from autonomy.decision_cycle.activation import read_step_activation
from autonomy.decision_cycle.memory.runner import MemoryRunner
from tests.autonomy.decision_cycle.memory.activation_fixtures import (
    _valid_payload,
    _write_payload,
)


class MemoryPluginIdentityTests(unittest.TestCase):
    def test_selected_id_may_differ_from_declared_plugin_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = MemoryRunner.from_activation(
                read_step_activation(_write_payload(tmp, _valid_payload("other_id")), "memory")
            )
            self.assertEqual(step.plugin_ids, ("other_id",))
            self.assertEqual(step.plugins[0].implementation.plugin_id, "recording_test")
            status = step.status()
            self.assertEqual(status["implementation_id"], "recording_test")
            self.assertEqual(status["plugins"][0]["plugin_id"], "other_id")
            self.assertEqual(status["plugins"][0]["implementation_id"], "recording_test")
            self.assertEqual(step.report()["plugins"][0]["implementation_id"], "recording_test")


if __name__ == "__main__":
    unittest.main()
