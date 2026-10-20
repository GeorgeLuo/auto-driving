from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
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
from autonomy.runtime.cycle_host import AutonomyCycleHost, sync_live_selection
from autonomy.runtime.execution import ControlExecution
from implementations.decision_cycle.catalog import packaged_activation
from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import LEDGER_KEY
from tests.support.action_fixtures import action_runner, plan_runner, proposal_runner

RECORDING_SPEC = "tests.shared.autonomy.decision_cycle.memory.activation_fixtures:_RecordingMemory"
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
    plan = steps.pop("plan", None) or plan_runner()
    host = AutonomyCycleHost(
        steps=replace(
            decision_steps(),
            proposal=proposal,
            plan=plan,
            action=action_runner(_PushyAction()),
            **steps,
        )
    )
    return host, proposal


def _state(report: dict) -> dict:
    return report["plugins"][-1]["state"]


class CycleHostMemoryTests(unittest.TestCase):
    def test_memory_receives_prior_applications(self):
        from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import _RecordingMemory

        seen = []
        original = _RecordingMemory.update
        def update(plugin, context, observation):
            seen.append(context)
            return original(plugin, context, observation)
        with tempfile.TemporaryDirectory() as tmp:
            host, _ = _host(memory=_memory_step(Path(tmp)))
            host.execution = ControlExecution(Mock(write=Mock(return_value={})))
            self.addCleanup(host.close)
            host.set_mode("autonomy")
            expected = host.execution.ticket().control_applications
            with patch.object(_RecordingMemory, "update", update):
                result = host.run(DecisionFrameContext("frame-history", 0, int(time.time() * 1000)))
            self.assertEqual(seen[0].shared_memory[CONTROL_APPLICATIONS_KEY], expected)
            self.assertNotIn("control_applications", result.context.to_dict())
        without_execution, _ = _host()
        self.addCleanup(without_execution.close)
        result = without_execution.run(DecisionFrameContext("frame-none", 0, int(time.time() * 1000)))
        self.assertNotIn(CONTROL_APPLICATIONS_KEY, result.context.shared_memory)

    def test_mode_and_applications_are_captured_in_the_same_ticket(self):
        host, _ = _host()
        host.execution = ControlExecution(Mock(write=Mock(return_value={})))
        self.addCleanup(host.close)
        host.set_mode("autonomy")
        original = host.execution.ticket
        def ticket_then_change_mode():
            ticket = original()
            host.set_mode("manual")
            return ticket
        with patch.object(host.execution, "ticket", side_effect=ticket_then_change_mode):
            result = host.run(DecisionFrameContext("f", 0, int(time.time() * 1000)))
        self.assertEqual(result.context.mode, "autonomy")
        self.assertEqual(result.context.shared_memory[CONTROL_APPLICATIONS_KEY][-1].mode, "autonomy")
        self.assertEqual(host.execution.ticket().control_applications[-1].mode, "manual")
        self.assertFalse(result.application.applied)

    def test_new_runs_and_memory_resets_clear_prior_applications(self):
        host, _ = _host()
        host.execution = ControlExecution(Mock(write=Mock(return_value={})))
        self.addCleanup(host.close)
        host.start()
        old = host.execution.ticket().control_applications[0]
        host.start()
        current = host.execution.ticket().control_applications
        self.assertEqual(len(current), 1)
        self.assertIsNot(current[0], old)
        self.assertEqual(current[0].reason, "awaiting-fresh-decision")
        host.reset_memory()
        self.assertEqual(host.execution.ticket().control_applications, ())
        self.assertNotIn(CONTROL_APPLICATIONS_KEY, host.shared_memory)
        result = host.run(DecisionFrameContext("after-reset", 0, int(time.time() * 1000)))
        self.assertEqual(result.context.shared_memory[CONTROL_APPLICATIONS_KEY], ())

    def test_failed_start_reports_its_error_instead_of_the_previous_run_error(self):
        host, _ = _host()
        host.stop(reason="error", error="earlier failure")
        original = host.set_mode
        def mode(value):
            if value != "manual":
                raise RuntimeError("new acquisition failure")
            return original(value)
        with patch.object(host, "set_mode", side_effect=mode):
            with self.assertRaisesRegex(RuntimeError, "new acquisition failure"):
                host.start()
        self.assertEqual(host.last_error, "RuntimeError: new acquisition failure")
        self.assertEqual(host.run_state, "error")
        with patch.object(host, "set_mode", return_value={}):
            host.start()
        self.assertIsNone(host.last_error)
        host.close()

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

    def test_bad_memory_selection_edit_keeps_the_applied_plugins_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = _memory_step(Path(tmp), extra_specs={"missing": "no_such_module:Nope"})
            host, proposal = _host(memory=step)
            applied = step.plugin_ids
            step.plugin_manager.select(["missing"])
            host.run(DecisionFrameContext("frame_x", 0, 1))
            self.assertEqual(step.plugin_ids, applied)
            self.assertIsNotNone(proposal.last_shared_memory)
            self.assertEqual(host.status()["error_count"], 0)
            selection = host.selection_status()
            self.assertEqual(selection["status"], "failed")
            self.assertIn("no_such_module", selection["error"])

    def test_default_steps_idle_while_memory_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = AutonomyCycleHost(
                steps=replace(
                    decision_steps(),
                    memory=_memory_step(Path(tmp)),
                    plan=plan_runner(),
                )
            )
            result = host.run(DecisionFrameContext("frame_i", 0, 1))
            self.assertEqual(result.control.reason, "no-selected-command")
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
            return {"schema": "memory_report_v1", "plugins": []}

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
    def test_an_inflight_success_cannot_clear_an_external_stop_error(self):
        from unittest import mock
        from autonomy.runtime.session import RunConfiguration
        from tests.deterministic.cli.automation_pipeline.cadence_fixtures import GatedPerception
        perception = GatedPerception(1)
        host = AutonomyCycleHost(steps=replace(decision_steps(), perception=perception), target=mock.Mock())
        errors = []
        def run():
            try:
                host.run(DecisionFrameContext("frame_1", 0, int(time.time() * 1000)))
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        try:
            host.start(RunConfiguration(mode="observe_only"))
            worker.start()
            perception.wait_started(0)
            host.stop(reason="error", error="simulator_state_changed: control source changed")
            perception.release_all()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(host.run_state, "error")
            self.assertIn("simulator_state_changed", host.last_error)
        finally:
            perception.release_all()
            worker.join(2)
            host.close()


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
        self.assertIsNone(host.steps.plan)
        self.assertEqual(host.steps.action.plugin_ids, ("mode",))
        result = host.run(DecisionFrameContext("frame_1", 1, 1, mode="autonomy"))
        self.assertEqual(result.proposal.status, "ok")
        self.assertEqual(result.action.authority.gate_id, "mode")
        self.assertEqual(result.control.reason, "mode-action-error")

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

    def test_status_answers_while_a_slow_step_holds_the_cycle(self) -> None:
        entered, release = threading.Event(), threading.Event()
        recording = _RecordingProposal()

        def slow_proposal(context, observation):
            if context.frame_id != "frame_1":
                entered.set()
                release.wait(5.0)
            return recording(context, observation)

        host, _ = _host(proposal=slow_proposal)
        host.run(DecisionFrameContext("frame_1", 0, 1_000))

        def busy_status(frame_id: str, index: int) -> dict:
            """Read status while ``frame_id`` holds the cycle, then let it finish."""
            entered.clear()
            release.clear()
            cycle = threading.Thread(
                target=host.run, args=(DecisionFrameContext(frame_id, index, 1_000 * (index + 1)),)
            )
            cycle.start()
            try:
                self.assertTrue(entered.wait(5.0))
                started = time.monotonic()
                status = host.status()
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual(status["cycle_in_progress"]["frame_id"], frame_id)
                return status["steps"]
            finally:
                release.set()
                cycle.join(5.0)

        # Back-to-back cycles never freed the lock for a read, yet the steps report.
        steps = busy_status("frame_2", 1)
        self.assertEqual(steps["action"]["plugin_ids"], list(host.steps.action.plugin_ids))
        # The turned-away read had frame_2 refresh the counters as it ended.
        self.assertEqual(busy_status("frame_3", 2)["action"]["run_count"], 2)
        self.assertIsNone(host.status()["cycle_in_progress"])
        self.assertEqual(host.status()["cycle_count"], 3)


def _uploaded_proposal(plugin_id: str, *, constructor: str = "pass") -> bytes:
    return (
        "from autonomy.decision_cycle.proposal.values import ActionProposal\n"
        "class Prototype:\n"
        f"    plugin_id = {plugin_id!r}\n"
        "    def __init__(self):\n"
        f"        {constructor}\n"
        "    def propose(self, source, shared_memory):\n"
        "        return ActionProposal(plugin_id=self.plugin_id, frame_id=source.frame_id,\n"
        "            lifecycle='inactive', freshness='none', confidence=0.0, reason='uploaded',\n"
        "            command=None, metadata={})\n"
    ).encode()


class CycleHostArmingTests(unittest.TestCase):
    def test_a_plugin_that_fails_to_construct_keeps_the_previous_selection(self) -> None:
        host = AutonomyCycleHost(steps=decision_steps())
        for plugin_id, constructor in (("working", "pass"), ("broken", "raise RuntimeError('cannot construct')")):
            host.catalog.upload(
                step="proposal", plugin_id=plugin_id, entrypoint="prototype:Prototype",
                source=_uploaded_proposal(plugin_id, constructor=constructor),
            )
        self.assertTrue(host.arm({"proposal": ["working"]})["ok"])

        failed = host.arm({"proposal": ["broken"]})

        self.assertEqual((failed["ok"], failed["status"]), (False, "failed"))
        self.assertRegex(failed["error"], r"^proposal: .*cannot construct")
        result = host.run(DecisionFrameContext("frame_1", 0, 1_000))
        self.assertEqual([item.plugin_id for item in result.proposal.candidates], ["working"])

    def test_an_unreadable_staged_activation_keeps_the_current_selection(self) -> None:
        loaded = packaged_activation("proposal")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            self.assertIsNone(sync_live_selection(None, path, loaded))
            path.write_text('{"schema": ', encoding="utf-8")
            self.assertIsNone(sync_live_selection(None, path, loaded))


if __name__ == "__main__":
    unittest.main(verbosity=2)
