"""Every cycle step reports and changes its live selection the same way."""

from __future__ import annotations

import unittest
from contextlib import closing
from dataclasses import replace

from autonomy.decision_cycle.activation import STEPS, step_activation_path, write_step_activation
from autonomy.decision_cycle.steps import decision_steps
from autonomy.decision_cycle.timing import step_timing
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import frame, write_plugin

# Restaged alternatives with unchanged plugin specs; observation and plan
# package one plugin each, so only arming an upload swaps them.
RESTAGED = {"perception": (), "memory": (), "proposal": (), "action": ("hold",)}


def hosts(activations):
    yield "chase", create_chase_host(ChaseSimCar(), steps=decision_steps(activations))
    yield "picar", create_picar_host(steps=decision_steps(activations))


def ids(state, phase, step):
    return [item["id"] for item in state[phase][step]]


def ran(host, step):
    return [item["plugin_id"] for item in step_timing(host.last_result.timing, step)["plugins"]]


class CatalogSelectionTests(unittest.TestCase):
    def setUp(self):
        self.activations = {step: packaged_activation(step) for step in STEPS}

    def test_both_hosts_arm_every_step_and_swap_a_one_plugin_step(self):
        swaps = {"observation": "prototype-observation", "plan": "prototype-plan", "action": "hold"}
        for vehicle, host in hosts(self.activations):
            with self.subTest(vehicle=vehicle), image_source(1) as root, closing(host):
                host.follow_activations(self.activations, root / "runtime")
                snapshot = host.plugin_catalog()["arming"]
                self.assertEqual(set(snapshot["applied"]), set(STEPS))
                self.assertEqual(set(snapshot["requested"]), set(STEPS))
                for step in STEPS:
                    self.assertEqual(ids(snapshot, "applied", step), list(host.step(step).plugin_ids))
                for step in ("observation", "plan"):
                    write_plugin(root / f"{step}.py", step, swaps[step], 1)
                    host.catalog.upload(step=step, plugin_id=swaps[step], entrypoint="prototype:Prototype",
                                        source=(root / f"{step}.py").read_bytes())
                session = host.session_status()

                armed = host.plugin_catalog({"operation": "arm",
                                             "selections": {step: [plugin_id] for step, plugin_id in swaps.items()}})
                self.assertEqual(armed["status"], "requested")
                for step, plugin_id in swaps.items():
                    self.assertEqual(ids(armed["arming"], "requested", step), [plugin_id])
                    self.assertEqual(ids(armed["arming"], "applied", step), ids(snapshot, "applied", step))
                self.assertEqual(host.session_status(), session)
                host.run(frame(root, 1))
                adopted = host.plugin_catalog()["arming"]
                self.assertEqual(adopted["status"], "applied")
                for step in STEPS:
                    expected = [swaps[step]] if step in swaps else ids(snapshot, "applied", step)
                    self.assertEqual(ids(adopted, "applied", step), expected)
                    self.assertEqual(ran(host, step), expected)

                for request, error in (
                    ({"operation": "arm", "selections": {"plan": ["highest_confidence", "prototype-plan"]}},
                     "the plan step runs exactly one plugin; 2 selected"),
                    ({"operation": "disarm", "selections": {"action": ["hold"]}},
                     "the action step runs exactly one plugin; 0 selected"),
                ):
                    refused = host.plugin_catalog(request)
                    self.assertFalse(refused["ok"])
                    self.assertIn(error, refused["error"])
                    self.assertEqual(refused["arming"]["requested"], adopted["applied"])
                    self.assertEqual(refused["arming"]["applied"], adopted["applied"])
                host.run(frame(root, 2))
                self.assertEqual((ran(host, "plan"), ran(host, "action")), (["prototype-plan"], ["hold"]))

    def test_both_hosts_apply_restaged_selections_on_the_next_frame(self):
        for vehicle, host in hosts(self.activations):
            with self.subTest(vehicle=vehicle), image_source(1) as root, closing(host):
                host.follow_activations(self.activations, root / "runtime")
                snapshot = host.plugin_catalog()["arming"]
                session = host.session_status()
                for step, plugins in RESTAGED.items():
                    write_step_activation(step_activation_path(root / "runtime", step),
                                          replace(self.activations[step], plugins=plugins))
                host.sync_selection()
                pending = host.plugin_catalog()["arming"]
                self.assertEqual(pending["status"], "requested")
                for step, plugins in RESTAGED.items():
                    self.assertEqual(ids(pending, "requested", step), list(plugins))
                    self.assertEqual(pending["applied"][step], snapshot["applied"][step])
                self.assertEqual(host.session_status(), session)
                host.run(frame(root, 1))
                adopted = host.plugin_catalog()["arming"]
                self.assertEqual(adopted["status"], "applied")
                for step in STEPS:
                    expected = list(RESTAGED[step]) if step in RESTAGED else ids(snapshot, "applied", step)
                    self.assertEqual(ids(adopted, "applied", step), expected)

                # A restage the step cannot run reports the failure and keeps its plugin.
                write_step_activation(step_activation_path(root / "runtime", "plan"),
                                      replace(self.activations["plan"], plugins=()))
                host.sync_selection()
                host.run(frame(root, 2))
                failed = host.plugin_catalog()["arming"]
                self.assertEqual(failed["status"], "failed")
                self.assertIn("the plan step runs exactly one plugin; 0 selected", failed["error"])
                self.assertEqual(ids(failed, "applied", "plan"), ["highest_confidence"])
                self.assertEqual(ran(host, "plan"), ["highest_confidence"])


if __name__ == "__main__":
    unittest.main()
