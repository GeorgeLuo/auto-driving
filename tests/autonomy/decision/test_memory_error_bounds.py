from __future__ import annotations
import json
import tempfile
import unittest
from autonomy.decision import (
    ActivatedMemoryStage,
    DecisionFrameContext,
    Observation,
    read_memory_activation,
)
from tests.autonomy.decision.memory_activation_fixtures import (
    _valid_payload,
    _write_payload,
)


class MemoryActivationTests(unittest.TestCase):
    def test_large_update_exception_keeps_bounded_status_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["fail_on_update"] = True
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 2_000
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            # Force an oversized exception string through the stage boundary.
            stage.implementation.fail_on_update = True
            original_update = stage.implementation.update

            def huge_fail(context, observation):
                del context, observation
                raise RuntimeError("x" * 300_000)

            stage.implementation.update = huge_fail  # type: ignore[method-assign]
            try:
                with self.assertRaises(RuntimeError):
                    stage.update(
                        DecisionFrameContext("frame_8", 8, 800),
                        Observation("obs_8", 790, {}),
                    )
            finally:
                stage.implementation.update = original_update  # type: ignore[method-assign]
            from autonomy.decision import DEFAULT_MAX_DIAGNOSTIC_CHARS
            # last_error/status must also be bounded (Chase worker publishes this).
            status = stage.status()
            self.assertIsNotNone(status["last_error"])
            self.assertLessEqual(
                len(status["last_error"]), DEFAULT_MAX_DIAGNOSTIC_CHARS
            )
            self.assertLessEqual(
                len(stage.last_error or ""), DEFAULT_MAX_DIAGNOSTIC_CHARS
            )
            self.assertEqual(status["last_error"], stage.last_error)
            # Status JSON itself must stay modest.
            status_bytes = len(json.dumps(status, sort_keys=True).encode("utf-8"))
            self.assertLess(status_bytes, 4_000)

    def test_update_failure_preserves_prior_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_NearCeilingThenFailMemory"
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 2_000
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            first = stage.update(
                DecisionFrameContext("frame_a", 1, 100),
                Observation("obs_a", 90, {}),
            )
            self.assertEqual(first.health, "empty")
            self.assertGreater(len(first.epoch_id), 1_000)
            with self.assertRaises(RuntimeError):
                stage.update(
                    DecisionFrameContext("frame_b", 2, 200),
                    Observation("obs_b", 190, {}),
                )
            self.assertEqual(stage.last_snapshot, first)

    def test_reset_failure_preserves_bounded_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            stage.implementation.fail_on_reset = True
            snapshot = stage.reset()
            self.assertEqual(snapshot.health, "empty")
            self.assertIn("reset exploded", snapshot.summary[0])
            self.assertIn("reset exploded", snapshot.metadata.get("reset_error", ""))
            self.assertIn("reset exploded", stage.last_error or "")
            self.assertEqual(stage.status()["last_error"], stage.last_error)

    def test_impossible_max_serialized_bytes_rejected_at_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 1
            with self.assertRaisesRegex(ValueError, "max_serialized_bytes must be >="):
                read_memory_activation(_write_payload(tmp, payload))

    def test_large_eviction_policy_rejected_when_fallback_cannot_fit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 512
            payload["memory"]["implementation_config"]["eviction_policy"] = "p" * 600
            with self.assertRaisesRegex(ValueError, "too small for framework failure"):
                read_memory_activation(_write_payload(tmp, payload))

    def test_near_threshold_bounds_still_isolate_reset_failure(self) -> None:
        """Activation probe must match live timestamp/identity width.

        A policy that fits the worst-case validated shape must still isolate
        reset failures without raising under the same ceiling.
        """
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"]["implementation_config"]["max_serialized_bytes"] = 512
            # Just under the activation-time capacity limit for these bounds.
            payload["memory"]["implementation_config"]["eviction_policy"] = "p" * 85
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            stage.implementation.fail_on_reset = True
            snapshot = stage.reset()
            self.assertEqual(snapshot.health, "empty")
            self.assertTrue(snapshot.epoch_id.startswith("epoch-reset-failed-"))
            self.assertEqual(len(snapshot.epoch_id.split("-")[-1]), 10)
            from autonomy.decision import serialized_memory_snapshot_bytes

            self.assertLessEqual(serialized_memory_snapshot_bytes(snapshot), 512)
            self.assertIn("reset exploded", stage.last_error or "")

    def test_broken_exception_str_still_records_update_and_isolates_reset_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["memory"][
                "implementation_spec"
            ] = "tests.autonomy.decision.memory_activation_fixtures:_BrokenStrMemory"
            stage = ActivatedMemoryStage(
                read_memory_activation(_write_payload(tmp, payload))
            )
            with self.assertRaises(Exception):
                stage.update(
                    DecisionFrameContext("f1", 1, 1),
                    Observation("o1", 1, {}),
                )
            self.assertIn("unprintable exception", stage.last_error or "")
            self.assertNotIn("stringification failed", stage.last_error or "")

            reset = stage.reset()
            self.assertEqual(reset.health, "empty")
            self.assertIn("unprintable exception", stage.last_error or "")

            snapped = stage.snapshot()
            self.assertEqual(snapped.health, "error")
            self.assertIn("unprintable exception", stage.last_error or "")
