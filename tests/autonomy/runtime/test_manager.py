from __future__ import annotations

import unittest
from unittest.mock import patch

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime import AutonomyControl, AutonomyManager
from autonomy.runtime.manager import EngineLoadError
from tests.support.action_fixtures import fixed_control_composition


def _context() -> DecisionFrameContext:
    return DecisionFrameContext(frame_id="frame_001", frame_index=1, timestamp_ms=10, mode="local")


class KnownGoodEngine:
    def reset(self) -> None:
        self.composition = fixed_control_composition(
            AutonomyControl(confidence=1.0, reason="known-good")
        )

    def describe_schema(self) -> dict[str, str]:
        return {"schema": "autonomy_engine_schema_v0", "engine_id": "known-good"}

    def act(self, context, perception, observation, memory):
        return self.composition.act(context, perception, observation, memory)


class FailOnceEngine:
    def reset(self) -> None:
        self.calls = 0
        self.composition = fixed_control_composition(
            AutonomyControl(steering=0.25, throttle=0.4, confidence=0.8, reason="recovered")
        )

    def describe_schema(self) -> dict[str, str]:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": "fail-once",
        }

    def act(self, context, perception, observation, memory):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient step failure")
        return self.composition.act(context, perception, observation, memory)


class RuntimeManagerTests(unittest.TestCase):
    def test_failed_reload_preserves_the_known_good_engine(self) -> None:
        manager = AutonomyManager(default_engine_spec=f"{__name__}:KnownGoodEngine")
        initial = manager.act(_context(), None, None, None)
        before = manager.status()

        with patch.object(
            manager,
            "_instantiate_engine",
            side_effect=RuntimeError("reload unavailable"),
        ):
            with self.assertRaisesRegex(EngineLoadError, "reload unavailable"):
                manager.reload_engine()

        failed = manager.status()
        self.assertEqual(initial.control.reason, "known-good")
        self.assertEqual(failed["engine"], before["engine"])
        self.assertEqual(failed["engine_config"], before["engine_config"])
        self.assertEqual(failed["engine_schema"], before["engine_schema"])
        self.assertEqual(failed["loaded_at_ms"], before["loaded_at_ms"])
        self.assertEqual(failed["last_step_at_ms"], before["last_step_at_ms"])
        self.assertEqual(failed["step_count"], before["step_count"])
        self.assertEqual(failed["last_control"], before["last_control"])
        self.assertEqual(failed["error_count"], before["error_count"] + 1)
        self.assertEqual(failed["last_error"], "RuntimeError: reload unavailable")

        recovered = manager.act(_context(), None, None, None)
        after_recovery = manager.status()
        self.assertEqual(recovered.control.reason, "known-good")
        self.assertEqual(after_recovery["step_count"], before["step_count"] + 1)
        self.assertEqual(after_recovery["error_count"], failed["error_count"])
        self.assertIsNone(after_recovery["last_error"])

    def test_engine_failure_holds_idle_with_an_error_result_and_allows_recovery(self) -> None:
        manager = AutonomyManager()
        engine_spec = f"{__name__}:FailOnceEngine"
        manager.load_engine(engine_spec)

        failed_action = manager.act(_context(), None, None, None)
        failed = manager.status()

        self.assertEqual(failed_action.status, "engine_error")
        self.assertEqual(failed_action.reason, "engine_internal_error")
        self.assertEqual(failed_action.control.steering, 0.0)
        self.assertEqual(failed_action.control.throttle, 0.0)
        self.assertFalse(failed_action.authority.proposed_applied)
        self.assertEqual(failed["step_count"], 0)
        self.assertIsNone(failed["last_step_at_ms"])
        self.assertEqual(failed["error_count"], 1)
        self.assertIn("transient step failure", failed["last_error"] or "")

        recovered_action = manager.act(_context(), None, None, None)
        recovered = manager.status()

        self.assertEqual(recovered_action.control.reason, "recovered")
        self.assertEqual(recovered_action.control.steering, 0.25)
        self.assertEqual(recovered_action.control.throttle, 0.4)
        self.assertEqual(recovered["step_count"], 1)
        self.assertIsNotNone(recovered["last_step_at_ms"])
        self.assertEqual(recovered["error_count"], 1)
        self.assertIsNone(recovered["last_error"])
        self.assertEqual(recovered["last_control"], recovered_action.control.to_dict())

    def test_idle_engine_takes_no_action(self) -> None:
        manager = AutonomyManager()

        self.assertIsNone(manager.act(_context(), None, None, None))
        self.assertEqual(manager.status()["last_control"]["reason"], "engine-idle")

    def test_status_provider_failure_is_isolated_from_runtime_state(self) -> None:
        manager = AutonomyManager()
        before = manager.status()

        manager.register_status_provider(
            "camera",
            lambda: {"status": "ready", "frames": 3},
        )

        def failed_provider() -> dict[str, object]:
            raise RuntimeError("status unavailable")

        manager.register_status_provider("perception", failed_provider)
        status = manager.status()

        self.assertEqual(status["components"]["camera"], {"status": "ready", "frames": 3})
        self.assertEqual(
            status["components"]["perception"],
            {"status": "error", "error": "RuntimeError: status unavailable"},
        )
        self.assertEqual(status["engine"], before["engine"])
        self.assertEqual(status["step_count"], before["step_count"])
        self.assertEqual(status["error_count"], before["error_count"])
        self.assertEqual(status["last_error"], before["last_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
