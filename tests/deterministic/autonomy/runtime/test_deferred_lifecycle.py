"""Deferred completion and retained policy history through real host and runner calls."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from autonomy.decision_cycle.activation import step_activation, selected_definitions
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.execution import ControlExecution
from autonomy.shared_memory import shared_memory_document, shared_memory_from_document
from implementations.decision_cycle.catalog import packaged_activation
from implementations.decision_cycle.shared.control_runs import read_control_runs
from implementations.decision_cycle.shared.policy import read_policy, read_exit, write_exit, follow_policy_plan
from tests.shared.autonomy.runtime.control_fixtures import RecordingTarget
from tests.support.action_fixtures import FIRST_ACTIVE_PLAN_SPEC
from tests.support.lifecycle_fixtures import (
    GATES, Gate, JUDGE_SPEC, EXECUTOR_SPEC, POLICY_SPEC, ACTOR_SPEC, CALL_KEY, ANSWERS_KEY,
)


def proposal(*plugins):
    specs = {"judge": JUDGE_SPEC, "executor": EXECUTOR_SPEC}
    return step_activation("proposal", list(plugins), specs)


def frame(host, index, *, now=None, **metadata):
    return host.run(DecisionFrameContext(f"f{index}", index, index if now is None else now, metadata=metadata))


class DeferredLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.gate = GATES["lifecycle"] = Gate()
        self.addCleanup(GATES.pop, "lifecycle")
        self.addCleanup(self.gate.finish)

    def host(self, *plugins):
        host = AutonomyCycleHost(steps=decision_steps({
            "proposal": proposal(*plugins),
            "plan": step_activation("plan", ["first_active"], {"first_active": FIRST_ACTIVE_PLAN_SPEC}),
        }))
        self.addCleanup(host.close)
        host.catalog.include(selected_definitions(packaged_activation("plan", ["highest_confidence"])))
        return host

    def test_completion_before_and_after_downstream_adoption_is_taken_once_from_current_map(self):
        for before in (True, False):
            with self.subTest(before=before):
                self.gate = GATES["lifecycle"] = Gate()
                self.addCleanup(self.gate.finish)
                host = self.host("judge")
                frame(host, 0)
                self.gate.wait_started()
                old_map = host.shared_memory
                self.assertTrue(host.arm({"plan": ["highest_confidence"]})["ok"])
                if before:
                    self.gate.finish()
                else:
                    frame(host, 1)  # real frame boundary adopts while the worker is pending
                self.assertEqual(old_map[CALL_KEY]["current"]["status"], "pending")
                self.gate.finish()
                frame(host, 2)
                self.assertIsNot(host.shared_memory, old_map)
                self.assertEqual(host.shared_memory[ANSWERS_KEY], ["answer:0"])
                self.assertEqual(host.shared_memory[CALL_KEY]["counts"]["abandoned"], 0)
                self.assertEqual(host.shared_memory[CALL_KEY]["counts"]["asked"], 1)
                frame(host, 3)
                self.assertEqual(host.shared_memory[ANSWERS_KEY], ["answer:0"])

    def test_completion_during_another_plugin_call_is_private_until_owner_runs(self):
        host = self.host("judge", "executor")
        frame(host, 0)
        self.gate.wait_started()
        self.gate.complete_during_executor = True
        frame(host, 1)
        records = host.steps.proposal.plugin_report()["plugins"]
        self.assertTrue(all(item["error"] is None for item in records), records)
        self.assertEqual(host.shared_memory[CALL_KEY]["current"]["status"], "pending")
        self.assertNotIn(ANSWERS_KEY, host.shared_memory)
        frame(host, 2)
        self.assertEqual(host.shared_memory[ANSWERS_KEY], ["answer:0"])
        # Real foreign writes are still rejected by the runner's ordinary guard.
        self.gate.foreign_write = True
        frame(host, 3)
        self.assertIn("RetainedStateError", host.steps.proposal.plugin_report()["plugins"][1]["error"])

    def test_another_plugin_cannot_commit_the_owners_completion(self):
        host = self.host("judge", "executor")
        frame(host, 0)
        self.gate.wait_started()
        self.gate.complete_during_executor = self.gate.foreign_take = True
        frame(host, 1)
        self.assertIn("owning plugin call", host.steps.proposal.plugin_report()["plugins"][1]["error"])
        self.assertEqual(host.shared_memory[CALL_KEY]["current"]["status"], "pending")

    def test_removing_and_readding_owner_cannot_revive_or_deliver_old_work(self):
        host = self.host("judge")
        frame(host, 0)
        self.gate.wait_started()
        old_map = host.shared_memory
        host.disarm({"proposal": ["judge"]})
        frame(host, 1)
        self.gate.finish()
        frame(host, 2)
        self.assertNotIn(CALL_KEY, host.shared_memory)
        self.assertNotIn(ANSWERS_KEY, host.shared_memory)
        self.assertEqual(old_map[CALL_KEY]["current"]["status"], "pending")
        host.arm({"proposal": proposal("judge").to_payload()})
        frame(host, 3)
        self.gate.wait_started(2)
        self.gate.finish()
        frame(host, 4)
        self.assertEqual(host.shared_memory[ANSWERS_KEY], ["answer:1"])
        self.assertEqual(host.shared_memory[CALL_KEY]["counts"]["asked"], 1)
        self.assertEqual(host.shared_memory[CALL_KEY]["counts"]["dropped"], 0)


class PolicyLifecycleTests(unittest.TestCase):
    def test_reconstructed_owner_ignores_retained_old_runs_exits_and_candidates_but_restored_state_matches(self):
        now = [1000]
        with patch("autonomy.runtime.execution.time.time", side_effect=lambda: now[0] / 1000):
            selected = {
                "proposal": step_activation("proposal", ["judge", "actor"], {"judge": POLICY_SPEC, "actor": ACTOR_SPEC}),
                "plan": packaged_activation("plan", ["follow_policy"]),
                "memory": packaged_activation("memory", ["control_runs"]),
            }
            host = AutonomyCycleHost(steps=decision_steps(selected))
            self.addCleanup(host.close)
            host.execution = ControlExecution(RecordingTarget())
            host.execution.set_mode("autonomy")
            first = frame(host, 0, now=now[0])
            self.assertTrue(first.application.applied)
            old_policy = read_policy(host.shared_memory)
            old_candidate = first.proposal.candidates[1]
            now[0] = 1001
            frame(host, 1, now=now[0])
            old_runs = read_control_runs(host.shared_memory)
            self.assertEqual(old_runs[0]["plan"]["policy"], old_policy.tag())
            # A downstream restage keeps the lifetime and the real applied history.
            host.catalog.include(selected_definitions(packaged_activation("plan", ["highest_confidence"])))
            self.assertTrue(host.arm({"plan": ["highest_confidence"]})["ok"])
            frame(host, 2, now=now[0])
            self.assertEqual(read_policy(host.shared_memory).tag(), old_policy.tag())
            old_judge = host.steps.proposal.applied[0][1]
            host.catalog.upload(step="proposal", plugin_id="actor", entrypoint="plugin:PolicyActor",
                                source=b"from tests.support.lifecycle_fixtures import PolicyActor\n")
            self.assertTrue(host.arm({"proposal": ["actor"]})["ok"])
            frame(host, 3, now=now[0])
            self.assertIsNot(host.steps.proposal.applied[0][1], old_judge)
            self.assertEqual(read_policy(host.shared_memory).tag(), old_policy.tag())
            host.catalog.upload(step="proposal", plugin_id="judge", entrypoint="plugin:PolicyJudge",
                                source=b"from tests.support.lifecycle_fixtures import PolicyJudge\n",
                                config={"revision": 1})
            self.assertTrue(host.arm({"plan": selected["plan"].to_payload(), "proposal": ["judge"]})["ok"])
            now[0] = 5000
            frame(host, 4, now=now[0], choose="approach")
            current = read_policy(host.shared_memory)
            self.assertNotEqual(current.lifetime, old_policy.lifetime)
            self.assertEqual((current.name, current.epoch), ("search", 0))
            self.assertEqual(read_control_runs(host.shared_memory)[0]["plan"], old_runs[0]["plan"])
            self.assertEqual(read_control_runs(host.shared_memory)[0]["start_ms"], old_runs[0]["start_ms"])
            write_exit(host.shared_memory, "actor", old_policy, why="done", now_ms=5000)
            self.assertIsNone(read_exit(host.shared_memory, "actor", current))
            stale = replace(old_candidate, frame_id="fresh", proposal_id="", metadata=old_candidate.to_dict()["metadata"])
            self.assertEqual(follow_policy_plan(DecisionFrameContext("fresh", 4, 5000, shared_memory=host.shared_memory), [stale]).status, "idle")
            now[0] = 5001
            frame(host, 5, now=now[0], choose="approach")
            self.assertEqual(read_policy(host.shared_memory).epoch, 0)
            # Seed and ControlApplication records round-trip the actual plan/control metadata.
            restored = shared_memory_from_document(shared_memory_document(host.shared_memory))
            from implementations.decision_cycle.shared.policy import PolicyCycle
            from tests.support.lifecycle_fixtures import POLICIES
            cycler = PolicyCycle(POLICIES, initial="search", key="proposal.judge.policy", min_acting_ms=100)
            self.assertEqual(cycler.current(restored, now_ms=5100).tag(), current.tag())
            self.assertEqual(cycler.choose(restored, "approach", now_ms=5099).epoch, 0)
            self.assertEqual(cycler.choose(restored, "approach", now_ms=5100).epoch, 1)
