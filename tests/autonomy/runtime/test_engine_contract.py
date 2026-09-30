from __future__ import annotations

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime import AutonomyControl, AutonomyManager
from autonomy.runtime.manager import EngineLoadError


class MissingResetEngine:
    def act(self, context, perception, observation):
        return None


class ControlOutputEngine:
    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, str]:
        return {"schema": "autonomy_engine_schema_v0"}

    def act(self, context, perception, observation):
        return AutonomyControl(reason="not-an-action-result")


class MissingSchemaEngine:
    def reset(self) -> None:
        return None

    def act(self, context, perception, observation):
        return None


class EngineContractTests(unittest.TestCase):
    def test_engine_requires_reset_method(self) -> None:
        manager = AutonomyManager()

        with self.assertRaises(EngineLoadError):
            manager.load_engine(f"{__name__}:MissingResetEngine")

    def test_engine_rejects_output_that_is_not_an_action_result(self) -> None:
        manager = AutonomyManager()
        manager.load_engine(f"{__name__}:ControlOutputEngine")

        action = manager.act(
            DecisionFrameContext(frame_id="frame", frame_index=0, timestamp_ms=0),
            None,
            None,
        )

        self.assertEqual(action.status, "engine_error")
        self.assertEqual(action.control.reason, "hold-idle")
        self.assertIn("must return ActionResult", manager.last_error or "")

    def test_engine_requires_schema_method(self) -> None:
        manager = AutonomyManager()

        with self.assertRaises(EngineLoadError):
            manager.load_engine(f"{__name__}:MissingSchemaEngine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
