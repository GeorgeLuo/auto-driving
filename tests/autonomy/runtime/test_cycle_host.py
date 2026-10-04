from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from autonomy.decision_cycle.action.values import ActionDecision
from autonomy.decision_cycle.activation import (
    STEP_ACTIVATION_SCHEMA,
    read_step_activation,
    step_activation_path,
    write_step_activation,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.cycle_host import AutonomyCycleHost
from implementations.decision_cycle.catalog import packaged_activation
from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import LEDGER_KEY
from tests.support.action_fixtures import action_runner, proposal_runner

RECORDING_SPEC = "tests.autonomy.decision_cycle.memory.activation_fixtures:_RecordingMemory"
STATE_KEY = "recording_test.state"


class _PushyAction:
    """Applies a fixed nonzero control so tests can see the action step's output."""

    plugin_id = "pushy"

    def decide(self, plan, *, mode, error_reason=None) -> ActionDecision:
        return ActionDecision(
            AutonomyControl(steering=0.7, throttle=0.4, confidence=1.0, reason="pushy-test-action")
        )


class _FailOnceAction:
    plugin_id = "fail_once"

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, plan, *, mode, error_reason=None) -> ActionDecision:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient action failure")
        return ActionDecision(AutonomyControl(steering=0.25, throttle=0.4, reason="recovered"))


class _RecordingProposal:
    """Proposal step that records what it saw and proposes nothing."""

    def __init__(self) -> None:
        self.runner = proposal_runner({})
        self.last_shared_memory: dict | None = None
        self.last_observation = None

    def __call__(self, context, observation):
        self.last_shared_memory = dict(context.shared_memory or {})
        self.last_observation = observation
        return self.runner(context, observation)


def _memory_step(
    root: Path, *, fail_on_update: bool = False, extra_specs: dict[str, str] | None = None
) -> MemoryRunner:
    path = root / "active.json"
    path.write_text(
        json.dumps(
            {
                "schema": STEP_ACTIVATION_SCHEMA,
                "step": "memory",
                "plugins": ["recording_test"],
                "plugin_specs": {"recording_test": RECORDING_SPEC, **(extra_specs or {})},
                "plugin_configs": {"recording_test": {"fail_on_update": fail_on_update}},
            }
        ),
        encoding="utf-8",
    )
    return MemoryRunner.from_activation(read_step_activation(path, "memory"))


def _host(**steps) -> tuple[AutonomyCycleHost, _RecordingProposal]:
    proposal = steps.pop("proposal", None) or _RecordingProposal()
    host = AutonomyCycleHost(
        steps=replace(
            decision_steps(),
            proposal=proposal,
            action=action_runner(_PushyAction()),
            **steps,
        )
    )
    return host, proposal


def _state(report: dict) -> dict:
    return report["plugins"][-1]["state"]


class CycleHostMemoryTests(unittest.TestCase):
    def test_cycle_result_report_is_detached_from_step_owned_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _memory_step(Path(tmp))
            host, _ = _host(
                observation=lambda context, perception: Observation(
                    observation_id="obs-1", created_at_ms=1, sensor_frame={}, summary=("test",)
                ),
                memory=step,
            )
            result = host.run(DecisionFrameContext("frame_1", 0, 1_000))
            owned = host.shared_memory[STATE_KEY]
            _state(result.memory)["records"][0]["properties"]["tamper"] = True
            _state(result.memory)["record_count"] = 99
            self.assertEqual(owned["records"][0].properties, {})
            self.assertEqual(_state(step.report())["record_count"], 1)

    def test_host_shares_the_map_with_later_steps_and_reports_step_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host, proposal = _host(memory=_memory_step(Path(tmp)))

            result = host.run(DecisionFrameContext("frame_1", 1, 100))

            self.assertEqual(_state(result.memory)["record_count"], 0)
            self.assertEqual(result.control.reason, "pushy-test-action")
            self.assertIn(STATE_KEY, proposal.last_shared_memory)
            status = host.status()
            self.assertEqual(
                sorted(status["steps"]),
                sorted(["perception", "observation", "memory", "proposal", "plan", "action"]),
            )
            self.assertIsNone(status["steps"]["perception"])
            self.assertEqual(status["steps"]["memory"]["plugin_ids"], ["recording_test"])
            self.assertEqual(
                status["steps"]["memory"]["plugins"][0]["state"]["epoch_id"], "epoch-1"
            )
            self.assertEqual(status["steps"]["action"]["plugin_ids"], ["pushy"])
            self.assertEqual(status["cycle_count"], 1)
            self.assertEqual(status["last_control"]["reason"], "pushy-test-action")
            self.assertIsNotNone(status["last_cycle"])

    def test_memory_failure_stops_before_later_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host, proposal = _host(memory=_memory_step(Path(tmp), fail_on_update=True))
            with self.assertRaisesRegex(MemoryUpdateError, "forced-update-failure"):
                host.run(DecisionFrameContext("frame_x", 0, 1))
            self.assertIsNone(proposal.last_shared_memory)
            self.assertIsNone(host.last_result)
            self.assertEqual(host.status()["error_count"], 1)
            self.assertIn("forced-update-failure", host.status()["last_error"])

    def test_bad_memory_selection_edit_stops_before_later_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _memory_step(Path(tmp), extra_specs={"missing": "no_such_module:Nope"})
            host, proposal = _host(memory=step)
            step.plugin_manager.select(["missing"])
            with self.assertRaisesRegex(MemoryUpdateError, "no_such_module"):
                host.run(DecisionFrameContext("frame_x", 0, 1))
            self.assertIsNone(proposal.last_shared_memory)
            self.assertEqual(host.status()["error_count"], 1)
            self.assertIn("no_such_module", host.status()["last_error"])

    def test_default_steps_hold_while_memory_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = AutonomyCycleHost(
                steps=replace(decision_steps(), memory=_memory_step(Path(tmp)))
            )
            result = host.run(DecisionFrameContext("frame_i", 0, 1))
            self.assertEqual(result.control.reason, "hold-idle")
            self.assertIsNone(result.proposal)
            self.assertEqual(result.plan.status, "idle")
            self.assertIsNotNone(result.memory)

    def test_host_reset_memory_clears_the_map_but_keeps_fresh_plugin_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _memory_step(Path(tmp))
            host, _ = _host(memory=step)
            host.run(DecisionFrameContext("frame_fill", 1, 100))
            step.update(
                DecisionFrameContext("frame_fill", 1, 100, shared_memory=host.shared_memory),
                Observation("obs_fill", 90, {}),
            )
            host.shared_memory["perception.history"] = "from an earlier frame"
            self.assertEqual(_state(step.report())["record_count"], 1)

            report = host.reset_memory()

            self.assertEqual(_state(report)["record_count"], 0)
            self.assertEqual(_state(report)["epoch_id"], "epoch-2")
            self.assertEqual(set(host.shared_memory), {STATE_KEY})
            self.assertEqual(
                host.status()["steps"]["memory"]["plugins"][0]["state"]["record_count"], 0
            )

    def test_host_reset_before_first_update_advances_the_ledger_epoch(self) -> None:
        step = MemoryRunner.from_activation(packaged_activation("memory"))
        host = AutonomyCycleHost(steps=replace(decision_steps(), memory=step))
        self.assertEqual(_state(step.report())["epoch_id"], "epoch-1")
        self.assertEqual(_state(host.reset_memory())["epoch_id"], "epoch-2")
        self.assertEqual(_state(host.reset_memory())["epoch_id"], "epoch-3")
        result = host.run(DecisionFrameContext("f1", 1, 100))
        self.assertEqual(_state(result.memory)["epoch_id"], "epoch-3")
        self.assertEqual(host.shared_memory[LEDGER_KEY].epoch_id, "epoch-3")

    def test_host_shares_the_map_across_cycles_and_delivers_the_observation(self) -> None:
        seen = []

        def observe(context, perception):
            seen.append(context.shared_memory.get("test.previous"))
            return Observation(context.frame_id, context.timestamp_ms, {})

        def remember(context, observation):
            context.shared_memory["test.previous"] = context.frame_id
            return {"schema": "memory_report_v0", "plugins": []}

        host, proposal = _host(observation=observe, memory=remember)
        for index in range(2):
            result = host.run(DecisionFrameContext(f"frame-{index}", index, index))
            self.assertIs(proposal.last_observation, result.observation)
            self.assertEqual(result.memory["plugins"], [])
        self.assertEqual(seen, [None, "frame-0"])

    def test_host_reset_memory_without_step_returns_none(self) -> None:
        host = AutonomyCycleHost()
        host.shared_memory["stale"] = True
        self.assertIsNone(host.reset_memory())
        self.assertEqual(host.shared_memory, {})


class CycleHostStepTests(unittest.TestCase):
    def test_runtime_activations_load_with_builtin_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_step_activation(
                step_activation_path(root, "proposal"), packaged_activation("proposal")
            )
            write_step_activation(
                step_activation_path(root, "action"), packaged_activation("action", ["mode"])
            )
            host = AutonomyCycleHost.from_runtime(root)

        self.assertIsNone(host.steps.perception)
        self.assertIsNone(host.steps.memory)
        self.assertEqual(host.steps.observation.plugin_ids, ("perception_summary",))
        self.assertEqual(host.steps.proposal.plugin_ids, ("avoid_recent_obstruction",))
        self.assertEqual(host.steps.plan.plugin_ids, ("highest_confidence",))
        self.assertEqual(host.steps.action.plugin_ids, ("mode",))
        result = host.run(DecisionFrameContext("frame_1", 1, 1, mode="autonomy"))
        self.assertEqual(result.proposal.status, "ok")
        self.assertEqual(result.action.authority.gate_id, "mode")
        self.assertEqual(result.control.reason, "no_selected_command")

    def test_action_failure_holds_with_an_error_record_and_recovers(self) -> None:
        action = _FailOnceAction()
        host, _ = _host()
        host.set_step("action", action_runner(action))

        failed = host.run(DecisionFrameContext("frame_1", 1, 1))
        self.assertEqual(failed.action.status, "error")
        self.assertEqual(failed.action.reason, "step_internal_error")
        self.assertEqual(failed.control.reason, "hold-idle")
        self.assertFalse(failed.action.authority.proposed_applied)
        self.assertIn("transient action failure", host.status()["steps"]["action"]["last_error"])

        recovered = host.run(DecisionFrameContext("frame_2", 2, 2))
        self.assertEqual(recovered.action.status, "ok")
        self.assertEqual(recovered.control.reason, "recovered")
        self.assertIsNone(host.status()["steps"]["action"]["last_error"])

    def test_empty_action_slot_uses_the_idle_reason(self) -> None:
        host = AutonomyCycleHost(steps=DecisionSteps())
        result = host.run(DecisionFrameContext("frame_1", 1, 1))
        self.assertIsNone(result.action)
        self.assertEqual(result.control.reason, "cycle-idle")

    def test_status_provider_failure_is_isolated(self) -> None:
        host = AutonomyCycleHost()
        host.register_status_provider("camera", lambda: {"status": "ready", "frames": 3})

        def failed_provider() -> dict:
            raise RuntimeError("status unavailable")

        host.register_status_provider("telemetry", failed_provider)
        status = host.status()
        self.assertEqual(status["components"]["camera"], {"status": "ready", "frames": 3})
        self.assertEqual(
            status["components"]["telemetry"],
            {"status": "error", "error": "RuntimeError: status unavailable"},
        )
        self.assertEqual(status["error_count"], 0)
        with self.assertRaises(ValueError):
            host.register_status_provider("", lambda: {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
