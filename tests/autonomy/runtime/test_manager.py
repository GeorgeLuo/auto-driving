from __future__ import annotations

import unittest
from unittest.mock import patch

from autonomy.runtime import AutonomyControl, AutonomyManager, AutonomySnapshot
from autonomy.runtime.manager import EngineLoadError


class FailOnceEngine:
    def reset(self) -> None:
        self.calls = 0

    def describe_schema(self) -> dict[str, str]:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": "fail-once",
        }

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        del snapshot
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient step failure")
        return AutonomyControl(
            steering=0.25,
            throttle=0.4,
            confidence=0.8,
            reason="recovered",
        )


class RuntimeManagerTests(unittest.TestCase):
    def test_failed_reload_preserves_the_known_good_engine(self) -> None:
        manager = AutonomyManager(default_engine_config={"reason": "known-good"})
        initial_control = manager.step(AutonomySnapshot(mode="local"))
        before = manager.status()

        with patch.object(
            manager,
            "_instantiate_engine",
            side_effect=RuntimeError("reload unavailable"),
        ):
            with self.assertRaisesRegex(EngineLoadError, "reload unavailable"):
                manager.reload_engine()

        failed = manager.status()
        self.assertEqual(initial_control.reason, "known-good")
        self.assertEqual(failed["engine"], before["engine"])
        self.assertEqual(failed["engine_config"], before["engine_config"])
        self.assertEqual(failed["engine_schema"], before["engine_schema"])
        self.assertEqual(failed["loaded_at_ms"], before["loaded_at_ms"])
        self.assertEqual(failed["last_step_at_ms"], before["last_step_at_ms"])
        self.assertEqual(failed["step_count"], before["step_count"])
        self.assertEqual(failed["last_control"], before["last_control"])
        self.assertEqual(failed["error_count"], before["error_count"] + 1)
        self.assertEqual(failed["last_error"], "RuntimeError: reload unavailable")

        recovered = manager.step(AutonomySnapshot(mode="local"))
        after_recovery = manager.status()
        self.assertEqual(recovered.reason, "known-good")
        self.assertEqual(after_recovery["step_count"], before["step_count"] + 1)
        self.assertEqual(after_recovery["error_count"], failed["error_count"])
        self.assertIsNone(after_recovery["last_error"])

    def test_step_failure_returns_idle_control_and_allows_recovery(self) -> None:
        manager = AutonomyManager()
        engine_spec = f"{__name__}:FailOnceEngine"
        manager.load_engine(engine_spec)

        failed_control = manager.step(AutonomySnapshot())
        failed = manager.status()

        self.assertEqual(failed_control.reason, "engine-error")
        self.assertEqual(failed_control.steering, 0.0)
        self.assertEqual(failed_control.throttle, 0.0)
        self.assertEqual(failed_control.confidence, 0.0)
        self.assertEqual(failed_control.metadata["engine"], engine_spec)
        self.assertIn("transient step failure", failed_control.metadata["error"])
        self.assertEqual(failed["step_count"], 0)
        self.assertIsNone(failed["last_step_at_ms"])
        self.assertEqual(failed["error_count"], 1)
        self.assertIn("transient step failure", failed["last_error"] or "")

        recovered_control = manager.step(AutonomySnapshot())
        recovered = manager.status()

        self.assertEqual(recovered_control.reason, "recovered")
        self.assertEqual(recovered_control.steering, 0.25)
        self.assertEqual(recovered_control.throttle, 0.4)
        self.assertEqual(recovered["step_count"], 1)
        self.assertIsNotNone(recovered["last_step_at_ms"])
        self.assertEqual(recovered["error_count"], 1)
        self.assertIsNone(recovered["last_error"])
        self.assertEqual(recovered["last_control"], recovered_control.to_dict())

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
