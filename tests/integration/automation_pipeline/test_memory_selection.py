from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cli.automa_cli.automation import run_vehicle_automation
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.step_activations import replace_metadata
from autonomy.decision_cycle.activation import read_step_activation, write_step_activation
from implementations.decision_cycle.catalog import packaged_activation
from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import BoundedEvidenceLedger
from tests.integration.automation_pipeline.pipeline_fixtures import (
    VEHICLE_ID,
    _SlowMapper,
    _write_activations,
    chase_runtime,
    staged_runners,
)


class SecondLedger(BoundedEvidenceLedger):
    plugin_id = "second"


def edit_staged_selection(path, plugin_id, enabled):
    """Add or remove one plugin in the staged activation, as an operator edit would."""

    activation = read_step_activation(path, "memory")
    manager = activation.plugin_manager()
    available = sorted(manager.available_ids)
    if enabled:
        manager.add(plugin_id)
    else:
        manager.remove(plugin_id)
    candidate = type(activation)(
        step="memory",
        plugins=tuple(manager.selected_ids),
        plugin_specs=activation.plugin_specs,
        plugin_configs=activation.plugin_configs,
    )
    write_step_activation(path, replace_metadata(candidate, dict(activation.metadata)))
    return available


class AutomationMemorySelectionTests(unittest.TestCase):
    def test_edits_to_the_staged_activation_change_plugins_on_the_next_cycle_of_the_running_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / VEHICLE_ID)
            _write_activations(bundle)
            payload = packaged_activation("memory", ["bounded_evidence"]).to_payload()
            payload["plugin_specs"]["second"] = f"{__name__}:SecondLedger"
            payload["plugin_configs"]["second"] = dict(payload["plugin_configs"]["bounded_evidence"])
            path = Path(bundle["memory_runtime_dir"]) / "active.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            applied = []
            outputs = []
            edits = []
            steps = []
            actions = [("second", True), ("bounded_evidence", False), ("second", False), ("bounded_evidence", True)]

            def wrap_memory(step_name, step):
                if step_name != "memory":
                    return
                steps.append(step)
                update = step.update

                def update_with_cli_edits(context, observation):
                    report = update(context, observation)
                    applied.append(step.plugin_ids)
                    plugins = (report or {}).get("plugins") or []
                    outputs.append(plugins[-1]["plugin_id"] if plugins else None)
                    if len(applied) <= len(actions):
                        plugin_id, enabled = actions[len(applied) - 1]
                        edits.append(edit_staged_selection(path, plugin_id, enabled))
                    return report

                step.update = update_with_cli_edits

            with (
                staged_runners(perception=_SlowMapper(), wrap=wrap_memory),
                chase_runtime(runtime_root),
            ):
                result = run_vehicle_automation(
                    vehicle_id=VEHICLE_ID, interval_s=0.4, num_decisions=5, take_control=False,
                )
            self.assertEqual(result.exit_code, 0, result.message)
            self.assertEqual(
                edits, [["bounded_evidence", "second"]] * 4
            )
            self.assertEqual(
                steps[0].status()["available_plugins"],
                ["bounded_evidence", "second"],
            )
            self.assertEqual(applied, [
                ("bounded_evidence",), ("bounded_evidence", "second"), ("second",), (), ("bounded_evidence",),
            ])
            self.assertEqual(outputs, ["bounded_evidence", "second", "second", None, "bounded_evidence"])
            self.assertEqual(len(steps), 1)
            state = json.loads((Path(bundle["runtime_dir"]) / "automation/state.json").read_text())
            self.assertEqual(state["processed_count"], 5)
            self.assertEqual(state["memory"]["status"]["plugin_ids"], ["bounded_evidence"])
