"""Every cycle step reports selection; only live-selectable steps accept arming."""

from __future__ import annotations

import unittest
from contextlib import closing
from dataclasses import replace

from autonomy.decision_cycle.activation import STEPS, step_activation_path, write_step_activation
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.plugin_catalog import ARMING_STEPS
from implementations.decision_cycle.catalog import default_activations
from implementations.runtime.chase_sim import create_host as create_chase_host
from implementations.runtime.picar import create_host as create_picar_host
from implementations.vehicle.chase_sim import ChaseSimCar
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import frame


class CatalogSelectionTests(unittest.TestCase):
    def test_both_hosts_report_applied_selections_for_every_step_without_expanding_arming(self):
        activations = default_activations()
        for vehicle in ("chase", "picar"):
            with self.subTest(vehicle=vehicle), image_source(1) as root, closing(create_chase_host(ChaseSimCar(), steps=decision_steps(activations))
                        if vehicle == "chase" else create_picar_host(steps=decision_steps(activations))) as host:
                host.follow_activations(activations, root / "runtime")
                snapshot = host.plugin_catalog()
                self.assertEqual(set(snapshot["arming"]["applied"]), set(STEPS))
                self.assertEqual(set(snapshot["arming"]["requested"]), set(STEPS))
                for step in STEPS:
                    expected = list(host.step(step).plugin_ids)
                    self.assertEqual([item["id"] for item in snapshot["arming"]["applied"][step]], expected)
                    if step not in ARMING_STEPS:
                        refused = host.plugin_catalog({"operation": "arm", "selections": {step: expected}})
                        self.assertFalse(refused["ok"])
                        self.assertEqual(host.plugin_catalog(), snapshot)
                session = host.session_status()
                for step in ARMING_STEPS:
                    write_step_activation(step_activation_path(root / "runtime", step), replace(activations[step], plugins=()))
                host.sync_selection()
                pending = host.plugin_catalog()["arming"]
                self.assertEqual(pending["status"], "requested")
                for step in ARMING_STEPS:
                    self.assertEqual(pending["requested"][step], [])
                    self.assertEqual(pending["applied"][step], snapshot["arming"]["applied"][step])
                self.assertEqual(host.session_status(), session)
                host.run(frame(root, 1))
                adopted = host.plugin_catalog()["arming"]
                self.assertEqual(adopted["status"], "applied")
                for step in STEPS:
                    expected = [] if step in ARMING_STEPS else snapshot["arming"]["applied"][step]
                    self.assertEqual(adopted["applied"][step], expected)


if __name__ == "__main__":
    unittest.main()
