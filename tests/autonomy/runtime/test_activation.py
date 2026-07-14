from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.runtime import AutonomyManager, apply_decision_activation, read_decision_activation


class DecisionActivationTests(unittest.TestCase):
    def test_activation_loads_and_applies_the_declared_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            activation_path.write_text(
                json.dumps(
                    {
                        "schema": "automa_decision_activation_v0",
                        "decision": {
                            "engine_id": "idle",
                            "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
                            "engine_config": {},
                        },
                    }
                ),
                encoding="utf-8",
            )

            activation = read_decision_activation(activation_path)
            manager = AutonomyManager()
            status = apply_decision_activation(manager, activation)

        self.assertEqual(activation.engine_id, "idle")
        self.assertEqual(status["engine"], "autonomy.runtime.engine:IdleAutonomyEngine")

    def test_activation_rejects_an_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            activation_path.write_text(
                json.dumps({"schema": "old_schema", "decision": {}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unsupported schema"):
                read_decision_activation(activation_path)

    def test_activation_file_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                read_decision_activation(Path(tmp) / "active.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
