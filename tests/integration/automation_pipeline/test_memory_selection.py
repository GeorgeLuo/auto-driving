from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.automation import run_vehicle_automation
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.memory import set_vehicle_memory_plugin
from cli.automa_cli.memory_runtime import load_memory_step_from_bundle
from implementations.decision_cycle.memory.catalog import (
    build_memory_activation_payload,
)
from tests.integration.automation_pipeline.pipeline_fixtures import _FakeCar, _SlowMapper, _write_activations


class AutomationMemorySelectionTests(unittest.TestCase):
    def test_cli_edits_change_plugins_on_next_cycle_in_same_worker(self):
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
                "from implementations.decision_cycle.memory.bounded_evidence.plugin import BoundedEvidenceLedger\n"
                "class SecondLedger(BoundedEvidenceLedger):\n"
                "    implementation_id = 'second'\n", encoding="utf-8",
            )
            payload = build_memory_activation_payload()
            memory = payload["memory"]
            memory.update({
                "plugins": ["bounded_evidence"],
                "plugin_specs": {
                    "bounded_evidence": memory["implementation_spec"],
                    "second": "implementations.decision_cycle.memory.second:SecondLedger",
                },
                "plugin_configs": {
                    name: dict(memory["implementation_config"])
                    for name in ("bounded_evidence", "second")
                },
            })
            payload["controller_bundle"] = {"root_dir": bundle["root_dir"]}
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

            def load_running_step(activation):
                step = load_memory_step_from_bundle(activation)
                steps.append(step)
                update = step.update

                def update_with_cli_edits(context, observation):
                    report = update(context, observation)
                    applied.append(step.plugin_ids)
                    plugins = (report or {}).get("plugins") or []
                    outputs.append(plugins[-1]["implementation_id"] if plugins else None)
                    if len(applied) <= len(actions):
                        plugin_id, enabled = actions[len(applied) - 1]
                        edits.append(set_vehicle_memory_plugin(
                            vehicle_id=vehicle_id, plugin_id=plugin_id,
                            enabled=enabled, json_output=True,
                        ))
                    return report

                step.update = update_with_cli_edits
                return step

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.memory.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation.discover_active_vehicles", return_value={}),
                patch("cli.automa_cli.automation.find_vehicle_by_id", return_value=(vehicle, None)),
                patch("cli.automa_cli.automation.ChaseSimCar", _FakeCar),
                patch("cli.automa_cli.automation._load_mapper", return_value=_SlowMapper()),
                patch("cli.automa_cli.automation.load_memory_step_from_bundle", side_effect=load_running_step),
            ):
                result = run_vehicle_automation(
                    vehicle_id=vehicle_id, interval_s=0.4, frames=5, take_control=False,
                )
            self.assertEqual(result.exit_code, 0, result.message)
            self.assertEqual([edit.exit_code for edit in edits], [0, 0, 0, 0], [edit.message for edit in edits])
            self.assertEqual(
                [json.loads(edit.message)["available_plugins"] for edit in edits],
                [["bounded_evidence", "second"]] * len(edits),
            )
            self.assertEqual(steps[0].status()["available_plugins"], ["bounded_evidence", "second"])
            self.assertEqual(applied, [
                ("bounded_evidence",), ("bounded_evidence", "second"), ("second",), (), ("bounded_evidence",),
            ])
            self.assertEqual(outputs, ["bounded_evidence", "second", "second", None, "bounded_evidence"])
            self.assertEqual(len(steps), 1)
            state = json.loads((Path(bundle["runtime_dir"]) / "automation/state.json").read_text())
            self.assertEqual(state["frames_processed"], 5)
            self.assertEqual(state["memory"]["status"]["plugin_ids"], ["bounded_evidence"])
