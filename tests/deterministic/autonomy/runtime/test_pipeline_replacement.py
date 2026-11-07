"""Requests replace an executable pipeline at one frame boundary."""
from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from autonomy.decision_cycle.activation import STEPS, selected_definitions, step_activation, write_step_activation
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.action.publication import (
    CONTROL_APPLICATIONS_KEY, MODE_KEY, USER_STEERING_KEY, USER_THROTTLE_KEY,
    read_control_applications, read_mode, read_user_steering, read_user_throttle,
)
from autonomy.decision_cycle.steps import STEP_RUNNERS, decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.execution import ControlExecution
from autonomy.runtime.plugin_loader import CodeSource, StagedStep
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.releases.paths import controller_bundle_paths
from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import RECORDING_SPEC
from tests.support.action_fixtures import FIRST_ACTIVE_PLAN_SPEC, INACTIVE_PROPOSAL_SPEC
from tests.support.perception_fixtures import local_perception

HOLD_SPEC = "autonomy.decision_cycle.action.hold:HoldAction"


def _proposal():
    return step_activation("proposal", ["inactive"], {"inactive": INACTIVE_PROPOSAL_SPEC})


def cycle(host, index=0, **kwargs):
    return host.run(DecisionFrameContext(f"frame-{index}", index, 1000 + index, **kwargs),
                    mode="observe_only", user_steering=0.25, user_throttle=0.5)


class PipelineReplacementTests(unittest.TestCase):
    def host(self):
        """A host of test plugins: perception runs a and b, and the catalog also offers c."""

        activations = {
            "perception": local_perception("a", "b"),
            "memory": step_activation("memory", ["recording_test"], {"recording_test": RECORDING_SPEC}),
            "proposal": _proposal(),
            "plan": step_activation("plan", ["first_active"], {"first_active": FIRST_ACTIVE_PLAN_SPEC}),
        }
        return AutonomyCycleHost(steps=decision_steps(activations))

    def test_perception_change_reconstructs_every_step_before_binding_a_fresh_map(self):
        host = self.host()
        host.execution = ControlExecution(Mock(write=Mock(return_value={})))
        host.set_mode("observe_only")
        self.addCleanup(host.close)
        cycle(host)
        applications = host.execution.ticket().control_applications
        steps, generation = host.steps, host.applied_decision()["generation_id"]
        old_map = host.shared_memory
        old_map["old-history"] = True
        self.assertTrue(host.disarm({"perception": ["b"]})["ok"])
        self.assertEqual(host.applied_decision()["generation_id"], generation)
        run = host.cycle.run

        def inspect(context):
            self.assertIs(context.shared_memory, host.shared_memory)
            self.assertIsNot(context.shared_memory, old_map)
            self.assertNotIn("old-history", context.shared_memory)
            self.assertEqual(read_mode(context.shared_memory), "observe_only")
            self.assertEqual(read_user_steering(context.shared_memory), 0.25)
            self.assertEqual(read_user_throttle(context.shared_memory), 0.5)
            self.assertEqual(read_control_applications(context.shared_memory), applications)
            self.assertEqual(set(context.shared_memory), {MODE_KEY, USER_STEERING_KEY, USER_THROTTLE_KEY, CONTROL_APPLICATIONS_KEY})
            for step in STEPS:
                self.assertIsNot(getattr(host.steps, step), getattr(steps, step))
            return run(context)

        with patch.object(host.cycle, "run", side_effect=inspect):
            cycle(host, 1, shared_memory=old_map)
        self.assertNotEqual(host.applied_decision()["generation_id"], generation)

    def test_any_construction_or_validation_failure_keeps_the_applied_pipeline(self):
        for step in STEPS:
            for hook in ("load_plugin", "validate_selection"):
                with self.subTest(step=step, hook=hook):
                    host = self.host()
                    cycle(host)
                    steps, memory, identity = host.steps, host.shared_memory, host.applied_decision()
                    with patch.object(STEP_RUNNERS[step], hook, side_effect=RuntimeError("replacement refused")):
                        request = host.disarm({"perception": ["b"]})
                    self.assertFalse(request["ok"])
                    self.assertIs(host.steps, steps)
                    self.assertIs(host.shared_memory, memory)
                    self.assertEqual(host.applied_decision(), identity)
                    cycle(host, 1)
                    self.assertIs(host.steps, steps)
                    self.assertEqual(host.error_count, 0)

    def test_failed_second_request_keeps_the_earlier_pending_build_and_its_error(self):
        host = self.host()
        original = host.steps
        host.disarm({"perception": ["b"]})
        pending = host._pending_steps
        with patch.object(STEP_RUNNERS["memory"], "validate_selection", side_effect=RuntimeError("second refused")):
            failed = host.disarm({"proposal": ["inactive"]})
        self.assertFalse(failed["ok"])
        self.assertIs(host._pending_steps, pending)
        self.assertIs(host.steps, original)
        cycle(host)
        self.assertIs(host.steps, pending)
        self.assertEqual(host.step("perception").plugin_ids, ("a",))
        self.assertIn("second refused", host.selection_status()["error"])

    def test_no_op_pending_reuse_and_cancellation_keep_history(self):
        host = self.host()
        cycle(host)
        host.shared_memory["history"] = marker = object()
        steps, memory, identity = host.steps, host.shared_memory, host.applied_decision()
        host.arm({"perception": ["a", "b"]})
        host.disarm({"perception": ["b"]})
        pending = host._pending_steps
        with patch("autonomy.runtime.cycle_host.decision_steps", side_effect=AssertionError("rebuilt pending")):
            host.disarm({"perception": ["b"]})
        self.assertIs(host._pending_steps, pending)
        host.arm({"perception": ["b"]})
        cycle(host, 1)
        self.assertIs(host.steps, steps)
        self.assertIs(host.shared_memory, memory)
        self.assertIs(host.shared_memory["history"], marker)
        self.assertEqual(host.applied_decision(), identity)

    def test_named_host_rearming_current_preset_preserves_runners_and_history(self):
        release = {"tree_sha256": "installed", "created_at_ms": 123}
        with patch("autonomy.runtime.cycle_host.INSTALLED_RELEASE", release):
            preset = local_perception("a", "b", preset="pair")
            host = AutonomyCycleHost(steps=decision_steps({"perception": preset}))
            host.note_host_name("one")
            cycle(host)
            steps, memory, identity = host.steps, host.shared_memory, host.applied_decision()
            memory["history"] = marker = object()
            with patch("autonomy.runtime.cycle_host.decision_steps", side_effect=AssertionError("rebuilt no-op")):
                request = host.arm({"perception": preset.to_payload()})
            self.assertEqual(request["status"], "applied")
            cycle(host, 1)
            self.assertIs(host.steps, steps)
            self.assertIs(host.shared_memory, memory)
            self.assertIs(memory["history"], marker)
            self.assertEqual(host.applied_decision(), identity)

    def test_named_host_repeated_preset_request_reuses_pending_pipeline(self):
        host = self.host()
        host.note_host_name("one")
        payload = local_perception("a", "b", "c", preset="triple").to_payload()
        self.assertTrue(host.arm({"perception": payload})["ok"])
        pending = host._pending_steps
        with patch("autonomy.runtime.cycle_host.decision_steps", side_effect=AssertionError("rebuilt pending")):
            request = host.arm({"perception": payload})
        self.assertEqual(request["status"], "requested")
        self.assertIs(host._pending_steps, pending)
        cycle(host)
        self.assertIs(host.steps, pending)

    def test_bundled_metadata_rearm_updates_runner_and_identity_without_rebuilding(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "vehicle")
            sync_controller_bundle(bundle, emit=None)
            # The framework's hold action, loaded from the bundle's copy.
            host = AutonomyCycleHost(steps=decision_steps(
                {"action": step_activation("action", ["hold"], {"hold": HOLD_SPEC})},
                source=CodeSource(bundle_root=Path(bundle["root_dir"])),
            ))
            cycle(host)
            runner = host.step("action")
            self.assertIsInstance(runner, StagedStep)
            steps, memory, identity = host.steps, host.shared_memory, host.applied_decision()
            memory["history"] = marker = object()
            renamed = replace(runner.activation, metadata={**runner.activation.metadata, "preset": "renamed"})
            request = host.arm({"action": renamed.to_payload()})
            self.assertEqual(request["status"], "applied")
            self.assertEqual(runner.activation.metadata["preset"], "renamed")
            self.assertEqual(host.applied_decision()["steps"]["action"]["metadata"]["preset"], "renamed")
            self.assertEqual(host.status()["steps"]["action"]["preset"], "renamed")
            cycle(host, 1)
            self.assertIs(host.steps, steps)
            self.assertIs(host.shared_memory, memory)
            self.assertIs(memory["history"], marker)
            self.assertEqual(host.applied_decision()["generation_id"], identity["generation_id"])

    def test_failed_metadata_assignment_keeps_cached_activation_and_identity(self):
        host = self.host()
        runner = host.step("proposal")
        original = runner.activation
        cached = host._applied_activations["proposal"]
        identity = host.applied_decision()
        renamed = replace(original, metadata={**original.metadata, "preset": "renamed"})

        def refuse(_runner, _activation):
            raise RuntimeError("metadata refused")

        with patch.object(type(runner), "activation", property(lambda _: original, refuse), create=True):
            with self.assertRaisesRegex(RuntimeError, "metadata refused"):
                host.arm({"proposal": renamed.to_payload()})
        self.assertIs(host._applied_activations["proposal"], cached)
        self.assertIs(runner.activation, original)
        self.assertEqual(host.applied_decision(), identity)

    def test_returning_to_the_same_executable_pipeline_returns_to_its_content_id(self):
        host = self.host()
        original = host.applied_decision()["generation_id"]
        host.disarm({"perception": ["b"]})
        cycle(host)
        self.assertNotEqual(host.applied_decision()["generation_id"], original)
        host.arm({"perception": ["b"]})
        cycle(host, 1)
        self.assertEqual(host.applied_decision()["generation_id"], original)

    def test_an_upload_alone_keeps_the_applied_pipeline_and_map(self):
        import base64
        host = self.host()
        cycle(host)
        steps, memory, identity = host.steps, host.shared_memory, host.applied_decision()
        receipt = host.plugin_catalog({
            "step": "proposal", "plugin_id": "uploaded", "entrypoint": "uploaded:Uploaded",
            "source_base64": base64.b64encode(b"class Uploaded:\n    plugin_id = 'uploaded'\n").decode(),
        })
        self.assertTrue(receipt["ok"])
        self.assertIs(host.steps, steps)
        cycle(host, 1)
        self.assertIs(host.steps, steps)
        self.assertIs(host.shared_memory, memory)
        self.assertEqual(host.applied_decision(), identity)

    def test_mode_changes_keep_runners_map_and_generation(self):
        host = self.host()
        host.execution = ControlExecution(Mock(write=Mock(return_value={})))
        self.addCleanup(host.close)
        cycle(host)
        steps, memory, identity = host.steps, host.shared_memory, host.applied_decision()
        host.set_mode("observe_only")
        cycle(host, 1)
        self.assertIs(host.steps, steps)
        self.assertIs(host.shared_memory, memory)
        self.assertEqual(host.applied_decision(), identity)

    def test_unchanged_empty_file_does_not_overwrite_a_later_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = self.host()
            loaded = _proposal()
            path = Path(tmp) / "active.json"
            host.watch_selection("proposal", path, loaded)
            write_step_activation(path, replace(loaded, plugins=()))
            cycle(host)
            self.assertEqual(host.step("proposal").plugin_ids, ())
            host.arm({"proposal": ["inactive"]})
            cycle(host, 1)
            self.assertEqual(host.step("proposal").plugin_ids, loaded.plugins)
            steps = host.steps
            cycle(host, 2)
            self.assertIs(host.steps, steps)

    def test_absent_step_stays_absent_on_disarm_and_can_be_armed(self):
        host = AutonomyCycleHost(steps=decision_steps({"perception": None}))
        host.catalog.include(selected_definitions(local_perception("a")))
        steps = host.steps
        with patch.object(STEP_RUNNERS["action"], "validate_selection", side_effect=RuntimeError("refused")):
            self.assertFalse(host.arm({"perception": ["a"]})["ok"])
        self.assertTrue(host.disarm({"perception": []}, all_plugins=True)["ok"])
        self.assertIsNone(host.selection_status()["error"])
        cycle(host)
        self.assertIs(host.steps, steps)
        self.assertIsNone(host.step("perception"))
        self.assertTrue(host.arm({"perception": ["a"]})["ok"])
        cycle(host, 1)
        self.assertEqual(host.step("perception").plugin_ids, ("a",))

    def test_a_callable_without_executable_definitions_cannot_be_carried_into_a_replacement(self):
        host = self.host()
        host = AutonomyCycleHost(steps=replace(host.steps, proposal=lambda context: None))
        steps = host.steps
        request = host.disarm({"perception": ["b"]})
        self.assertFalse(request["ok"])
        self.assertIn("cannot be reconstructed", request["error"])
        self.assertIs(host.steps, steps)

    def test_status_during_the_first_new_cycle_has_new_runner_reports_and_no_old_result(self):
        host = self.host()
        cycle(host)
        previous = host.applied_decision()["generation_id"]
        host.disarm({"perception": ["b"]})
        entered, release = threading.Event(), threading.Event()
        run = host.cycle.run
        errors = []

        def block(context):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release the cycle")
            return run(context)

        def worker():
            try:
                cycle(host, 1)
            except BaseException as exc:
                errors.append(exc)

        with patch.object(host.cycle, "run", side_effect=block):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                status = host.status()
                self.assertNotEqual(status["applied_decision"]["generation_id"], previous)
                self.assertEqual(status["steps"]["memory"]["run_count"], 0)
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
