from __future__ import annotations
import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from autonomy.decision_cycle.observation.publication import OBSERVATION_KEY
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.runner import DEFAULT_MAX_DIAGNOSTIC_CHARS, MemoryRunner
from autonomy.decision_cycle.memory.interface import FAILURE_POLICY
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from autonomy.decision_cycle.activation import read_step_activation
from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import (
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
    def test_missing_observation_follows_policy_and_schema_matches_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(tmp)
            runtime = runner.plugins["recording_test"]
            self.assertEqual(runner.describe_schema()["inputs"][0],
                             ProposalRunner.from_plugins({}).describe_schema()["inputs"][0])
            for policy, observation, called in (
                ("invoke", None, True), ("skip_plugin", None, False),
                ("skip_plugin", Observation("obs", 90, {}), True),
            ):
                with self.subTest(policy=policy, observation=observation), patch(
                    "autonomy.decision_cycle.memory.runner.FAILURE_POLICY",
                    replace(FAILURE_POLICY, missing_input=policy),
                ):
                    previous = runtime.update_count
                    shared = {} if observation is None else {OBSERVATION_KEY: observation}
                    runner.update(DecisionFrameContext("frame", 0, 100, shared_memory=shared))
                    self.assertEqual(runtime.update_count - previous, int(called))
                    expected = "plugins are still called" if policy == "invoke" else "plugins are skipped"
                    self.assertIn(expected, runner.describe_schema()["inputs"][0]["missing_behavior"])

    def test_large_update_exception_keeps_bounded_status_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _runner(tmp)

            def huge_fail(context):
                del context
                raise RuntimeError("x" * 300_000)

            step.plugins["recording_test"].implementation.update = huge_fail  # type: ignore[method-assign]
            with self.assertRaises(RuntimeError):
                context = DecisionFrameContext("frame_8", 8, 800, shared_memory={})
                context.shared_memory[OBSERVATION_KEY] = Observation("obs_8", 790, {})
                step.update(context)
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
            context = DecisionFrameContext("frame_a", 1, 100, shared_memory=shared)
            context.shared_memory[OBSERVATION_KEY] = Observation("obs_a", 90, {})
            step.update(context)
            before = dict(shared)
            step.plugins["recording_test"].implementation.fail_on_update = True
            with self.assertRaises(RuntimeError):
                context = DecisionFrameContext("frame_b", 2, 200, shared_memory=shared)
                context.shared_memory[OBSERVATION_KEY] = Observation("obs_b", 190, {})
                step.update(context)
            self.assertEqual(
                {key: value for key, value in shared.items() if key != OBSERVATION_KEY},
                {key: value for key, value in before.items() if key != OBSERVATION_KEY},
            )
            self.assertEqual(step.report()["plugins"][0]["state"]["records"][0]["record_id"], "rec-obs_a")

    def test_reset_failure_is_recorded_with_a_bounded_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _runner(tmp)
            step.plugins["recording_test"].implementation.fail_on_reset = True

            self.assertEqual(step.reset({}), {})

            self.assertIn("reset exploded", step.last_error or "")
            self.assertEqual(step.status()["last_error"], step.last_error)

