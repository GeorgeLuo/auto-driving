from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.automation import run_vehicle_automation
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.step_activations import replace_metadata
from autonomy.decision_cycle.activation import read_step_activation, write_step_activation
from implementations.decision_cycle.catalog import packaged_activation
from tests.integration.automation_pipeline.pipeline_fixtures import (
    _FakeCar,
    _SlowMapper,
    _write_activations,
    staged_runners,
)


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
    def test_edits_to_the_staged_activation_change_plugins_on_next_cycle_in_same_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            bundle = controller_bundle_paths(runtime_root / vehicle_id)
            sync_controller_bundle(bundle, output=None)
            _write_activations(bundle)
            # This implementation exists only in the staged bundle and is first
            # loaded after automation starts, exercising the lazy import context.
            staged = Path(bundle["root_dir"]) / "implementations/decision_cycle/memory/second.py"
            staged.write_text(
                "from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import BoundedEvidenceLedger\n"
                "class SecondLedger(BoundedEvidenceLedger):\n"
                "    plugin_id = 'second'\n"
                "    plugin_id = 'second'\n", encoding="utf-8",
            )
            payload = packaged_activation("memory", ["bounded_evidence"]).to_payload()
            config = payload["plugin_configs"]["bounded_evidence"]
            payload["plugin_specs"]["second"] = "implementations.decision_cycle.memory.second:SecondLedger"
            payload["plugin_configs"]["second"] = dict(config)
            payload["metadata"] = {"controller_bundle": {"root_dir": bundle["root_dir"]}}
            path = Path(bundle["memory_runtime_dir"]) / "active.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            vehicle = {
                "id": vehicle_id, "provider": "chase-sim",
                "connection": {"ws_url": "ws://unused"},
                "status": {"passive_capture": {"status": "available", "session_preservation": {
                    "preserved": True, "unknown_fields": [], "changed_fields": [],
                }}},
            }
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
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation.discover_active_vehicles", return_value={}),
                patch("cli.automa_cli.automation.find_vehicle_by_id", return_value=(vehicle, None)),
                patch("cli.automa_cli.automation.ChaseSimCar", _FakeCar),
                staged_runners(perception=_SlowMapper(), wrap=wrap_memory),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id, interval_s=0.4, frames=5, take_control=False,
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
            self.assertEqual(state["frames_processed"], 5)
            self.assertEqual(state["memory"]["status"]["plugin_ids"], ["bounded_evidence"])
