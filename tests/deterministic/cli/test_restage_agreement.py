"""Host arms and both replay surfaces adopt the same surviving map."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from autonomy.decision_cycle.activation import STEPS, step_activation, selected_definitions
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from cli.automa_cli.plugins.inspect.replay import StepReplay
from tests.deterministic.cli.test_replay_pipeline import recording
from tests.shared.cli.workbench_fixtures import ImageReplayRunner, image_source, _wait_until
from tests.support.action_fixtures import INACTIVE_PROPOSAL_SPEC, FIRST_ACTIVE_PLAN_SPEC
from tests.support.perception_fixtures import local_perception


class RestageAgreementTests(unittest.TestCase):
    def test_downstream_arms_and_perception_append_remove_and_reorder_keep_the_same_keys(self):
        with image_source(4) as root:
            source, selected = recording(root)
            selected.update(
                perception=local_perception("a", "b"),
                proposal=step_activation("proposal", ["inactive"], {"inactive": INACTIVE_PROPOSAL_SPEC}),
                plan=step_activation("plan", ["first_active"], {"first_active": FIRST_ACTIVE_PLAN_SPEC}),
            )
            host = AutonomyCycleHost(steps=decision_steps(selected))
            self.addCleanup(host.close)
            replay = StepReplay(source, steps=STEPS, overrides=selected)
            replay.overrides.clear()
            workbench = ImageReplayRunner(root, activations={key: selected[key] for key in ("perception", "memory", "proposal", "plan")}, cadence_ms=30000)
            self.addCleanup(workbench.close)
            workbench.catalog.include(selected_definitions(local_perception("a", "b", "c")))
            run_id = workbench.start()["run_id"]
            _wait_until(lambda: workbench.state()["position"] == 1)
            workbench.dispatch("pause", run_id=run_id)
            keys = {
                "perception.a.history", "perception.b.history", "observation.current",
                "memory.recording_test.state", "memory.evidence", "proposal.inactive.history",
                "plan.first_active.history", "action.selected.history", "seed",
                "action.mode", "action.user_steering", "action.user_throttle", "action.control_applications",
            }
            maps = (host.shared_memory, replay.shared_memory, workbench._shared_memory)
            for shared in maps:
                shared.clear()
                shared.update({key: object() for key in keys})
            arms = (("proposal", ()), ("perception", ("a", "b", "c")),
                    ("perception", ("a", "c", "b")), ("memory", ()))
            with workbench._action_lock:
                workbench.dispatch("resume", run_id=run_id)
                for index, (step, plugins) in enumerate(arms):
                    with self.subTest(step=step, plugins=plugins):
                        activation = local_perception(*plugins) if step == "perception" else replace(selected[step], plugins=plugins)
                        selected[step] = activation
                        payload = {step: activation.to_payload()}
                        self.assertTrue(host.arm(payload)["ok"])
                        # A write after construction must be included at adoption.
                        for shared in (host.shared_memory, replay.shared_memory, workbench._shared_memory):
                            shared["late-seed"] = object()
                        host._adopt_pending()
                        frame = replace(source.frames[index], metadata={"steps": {
                            name: item.to_payload() if item else None for name, item in selected.items()
                        }})
                        replay.restage(frame)
                        workbench.dispatch("select_plugins", run_id=run_id, step=step, active_plugin_ids=list(plugins))
                        self.assertEqual(set(host.shared_memory), set(replay.shared_memory))
                        self.assertEqual(set(host.shared_memory), set(workbench._shared_memory))
                        self.assertIn("late-seed", host.shared_memory)
                        if index == 0:
                            self.assertIn("memory.recording_test.state", host.shared_memory)
                        if index == 1:
                            self.assertIn("perception.b.history", host.shared_memory)
                            self.assertNotIn("observation.current", host.shared_memory)
                        if index == 2:
                            self.assertNotIn("perception.b.history", host.shared_memory)
                workbench.dispatch("pause", run_id=run_id)

    def test_host_filters_writes_from_an_in_flight_frame_when_adopting(self):
        host = AutonomyCycleHost(steps=decision_steps({"perception": local_perception("a", "b")}))
        self.addCleanup(host.close)
        before = host.steps
        host.disarm({"perception": ["b"]})
        host.shared_memory.update({"perception.a.history": object(), "perception.b.history": object()})
        with patch.object(host.cycle, "run", wraps=host.cycle.run) as run:
            host.run(DecisionFrameContext("f", 0, 100))
        received = run.call_args.args[0].shared_memory
        self.assertIn("perception.a.history", received)
        self.assertNotIn("perception.b.history", received)
        for step in STEPS[1:]:
            self.assertIs(getattr(host.steps, step), getattr(before, step))
