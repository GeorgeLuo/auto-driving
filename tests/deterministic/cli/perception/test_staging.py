from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from cli.automa_cli.step_activations import CONTROLLER_BUNDLE_KEYS


class PerceptionCommandTests(unittest.TestCase):


    def test_update_perception_and_memory_share_staging_provenance(self) -> None:
        from unittest.mock import patch

        from cli.automa_cli import memory as memory_module
        from cli.automa_cli import perception as perception_module

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with (
                patch.object(perception_module, "RUNTIME_ROOT", runtime_root),
                patch.object(memory_module, "RUNTIME_ROOT", runtime_root),
                patch.object(
                    perception_module,
                    "get_vehicle_status",
                    return_value={
                        "vehicle_id": "chase-sim-chaser",
                        "layers": {},
                        "readiness": {},
                    },
                ),
            ):
                perception_module.update_vehicle_perception(
                    vehicle_id="chase-sim-chaser", timeout_s=0.2, json_output=True,
                )
                memory_module.update_vehicle_memory(
                    vehicle_id="chase-sim-chaser", timeout_s=0.2, json_output=True,
                )
            perception = json.loads(
                (runtime_root / "chase-sim-chaser/bundle/runtime/perception/active.json").read_text()
            )
            memory = json.loads(
                (runtime_root / "chase-sim-chaser/bundle/runtime/memory/active.json").read_text()
            )

        self.assertEqual(set(perception["metadata"]), set(memory["metadata"]))
        perception_bundle = perception["metadata"]["controller_bundle"]
        memory_bundle = memory["metadata"]["controller_bundle"]
        self.assertEqual(set(perception_bundle), set(CONTROLLER_BUNDLE_KEYS))
        self.assertEqual(set(memory_bundle), set(CONTROLLER_BUNDLE_KEYS))
        for key in ("root_dir", "autonomy_dir", "implementations_dir", "runtime_dir"):
            self.assertEqual(perception_bundle[key], memory_bundle[key])
        self.assertEqual(
            perception_bundle["release"]["tree_sha256"],
            memory_bundle["release"]["tree_sha256"],
        )
        for key in ("vehicle_id", "provider", "vehicle_kind"):
            self.assertEqual(perception["metadata"][key], memory["metadata"][key])
        self.assertEqual(perception["metadata"]["runtime"], memory["metadata"]["runtime"])
        for document in (perception, memory):
            self.assertNotIn("source_dir", document["metadata"])
            self.assertNotIn("workspace_source_dir", document["metadata"])
            self.assertNotIn("preset_description", document["metadata"])
            self.assertIn("preset", document["metadata"])
