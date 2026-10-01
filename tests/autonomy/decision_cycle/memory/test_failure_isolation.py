from __future__ import annotations
import json
import tempfile
import unittest
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.execution.plugin_runtime import DEFAULT_MAX_DIAGNOSTIC_CHARS
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.activation import read_step_activation
from tests.autonomy.decision_cycle.memory.activation_fixtures import (
    _valid_payload,
    _write_payload,
)


def _runner(tmp: str, spec: str | None = None) -> MemoryRunner:
    payload = _valid_payload()
    if spec is not None:
        payload["plugin_specs"]["recording_test"] = spec
    return MemoryRunner.from_activation(
        read_step_activation(_write_payload(tmp, payload), "memory")
    )


class MemoryFailureIsolationTests(unittest.TestCase):
    def test_large_update_exception_keeps_bounded_status_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _runner(tmp)

            def huge_fail(context, observation):
                del context, observation
                raise RuntimeError("x" * 300_000)

            step.plugins[0].implementation.update = huge_fail  # type: ignore[method-assign]
            with self.assertRaises(RuntimeError):
                step.update(
                    DecisionFrameContext("frame_8", 8, 800, shared_memory={}),
                    Observation("obs_8", 790, {}),
                )
            # last_error/status must be bounded (Chase worker publishes this).
            status = step.status()
            self.assertIsNotNone(status["last_error"])
            self.assertLessEqual(len(status["last_error"]), DEFAULT_MAX_DIAGNOSTIC_CHARS)
            self.assertLessEqual(len(step.last_error or ""), DEFAULT_MAX_DIAGNOSTIC_CHARS)
            self.assertEqual(status["last_error"], step.last_error)
            # The runner, plugin status, and plugin report each carry the bounded error.
            status_bytes = len(json.dumps(status, sort_keys=True).encode("utf-8"))
            self.assertLess(status_bytes, 3 * DEFAULT_MAX_DIAGNOSTIC_CHARS + 2_000)

    def test_update_failure_leaves_the_plugin_state_as_it_was(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _runner(tmp)
            shared: dict = {}
            step.update(
                DecisionFrameContext("frame_a", 1, 100, shared_memory=shared),
                Observation("obs_a", 90, {}),
            )
            before = dict(shared)
            step.plugins[0].implementation.fail_on_update = True
            with self.assertRaises(RuntimeError):
                step.update(
                    DecisionFrameContext("frame_b", 2, 200, shared_memory=shared),
                    Observation("obs_b", 190, {}),
                )
            self.assertEqual(shared, before)
            self.assertEqual(step.report()["plugins"][0]["state"]["records"][0]["record_id"], "rec-obs_a")

    def test_reset_failure_is_recorded_with_a_bounded_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _runner(tmp)
            step.plugins[0].implementation.fail_on_reset = True

            self.assertEqual(step.reset({}), {})

            self.assertIn("reset exploded", step.last_error or "")
            self.assertEqual(step.status()["last_error"], step.last_error)

    def test_broken_exception_str_is_recorded_without_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _runner(
                tmp, "tests.autonomy.decision_cycle.memory.activation_fixtures:_BrokenStrMemory"
            )
            with self.assertRaises(Exception):
                step.update(
                    DecisionFrameContext("f1", 1, 1, shared_memory={}),
                    Observation("o1", 1, {}),
                )
            self.assertIn("unprintable exception", step.last_error or "")
            self.assertNotIn("stringification failed", step.last_error or "")

            step.reset({})
            self.assertIn("unprintable exception", step.last_error or "")

            state = step.report()["plugins"][0]["state"]
            self.assertIn("unprintable exception", state["status_error"])
