from __future__ import annotations

import unittest

from autonomy.runtime import AutonomyControl, AutonomyManager, AutonomySnapshot
from autonomy.runtime.manager import EngineLoadError


class MissingResetEngine:
    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        return AutonomyControl(reason="missing-reset")


class DictionaryOutputEngine:
    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, str]:
        return {"schema": "autonomy_engine_schema_v0"}

    def step(self, snapshot: AutonomySnapshot) -> dict[str, float]:
        return {"steering": 0.0, "throttle": 0.0}


class MissingSchemaEngine:
    def reset(self) -> None:
        return None

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        return AutonomyControl(reason="missing-schema")


class EngineContractTests(unittest.TestCase):
    def test_engine_requires_reset_method(self) -> None:
        manager = AutonomyManager()

        with self.assertRaises(EngineLoadError):
            manager.load_engine(f"{__name__}:MissingResetEngine")

    def test_engine_rejects_dictionary_control_output(self) -> None:
        manager = AutonomyManager()
        manager.load_engine(f"{__name__}:DictionaryOutputEngine")

        control = manager.step(AutonomySnapshot())

        self.assertEqual(control.reason, "engine-error")
        self.assertIn("must return AutonomyControl", manager.last_error or "")

    def test_engine_requires_schema_method(self) -> None:
        manager = AutonomyManager()

        with self.assertRaises(EngineLoadError):
            manager.load_engine(f"{__name__}:MissingSchemaEngine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
