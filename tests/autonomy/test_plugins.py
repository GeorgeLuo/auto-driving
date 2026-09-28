from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from autonomy.plugins import (
    LocalPluginCatalog,
    PluginDefinition,
    PluginManagementError,
    PluginManager,
)
from implementations.decision.proposals.avoid_recent_obstruction import (
    DEFAULT_ACCEPTED_KINDS,
    DEFAULT_RETAINED_MAX_AGE_MS,
    DEFAULT_STEER_MAGNITUDE,
    PLUGIN_ID as PROPOSAL_PLUGIN_ID,
    propose as avoid_recent_obstruction,
)
from implementations.memory import BoundedEvidenceLedger, memory_implementation_spec
from implementations.perception.catalog import PERCEPTION_PLUGIN_SPECS
from implementations.perception.observation.plugin import FrameObservationPlugin


def _load_entrypoint(entrypoint: str):
    module_name, separator, symbol_name = entrypoint.partition(":")
    if not separator or not module_name or not symbol_name:
        raise ValueError(f"invalid entrypoint {entrypoint!r}")
    return getattr(importlib.import_module(module_name), symbol_name)


class PluginDefinitionTests(unittest.TestCase):
    def test_file_definition_preserves_scope_reference_and_config(self) -> None:
        payload = {
            "step": "memory",
            "id": "bounded_evidence",
            "entrypoint": (
                "implementations.memory.bounded_evidence:BoundedEvidenceLedger"
            ),
            "config": {"max_records": 12},
            "metadata": {"description": "bounded ledger"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounded-evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            manager = PluginManager("memory", LocalPluginCatalog())
            selected = manager.select([path])
            definition = selected[0]

        self.assertEqual(definition.step, "memory")
        self.assertEqual(definition.plugin_id, "bounded_evidence")
        self.assertEqual(
            definition.entrypoint,
            "implementations.memory.bounded_evidence:BoundedEvidenceLedger",
        )
        self.assertEqual(definition.config, {"max_records": 12})
        self.assertEqual(definition.metadata, {"description": "bounded ledger"})
        self.assertEqual(definition.source, path.resolve())


class LocalPluginCatalogTests(unittest.TestCase):
    def test_same_id_is_resolved_within_its_step_scope(self) -> None:
        perception = PluginDefinition(
            step="perception",
            plugin_id="shared",
            entrypoint="tests.fixtures.perception:Plugin",
        )
        memory = PluginDefinition(
            step="memory",
            plugin_id="shared",
            entrypoint="tests.fixtures.memory:Plugin",
        )
        catalog = LocalPluginCatalog([perception, memory])

        self.assertEqual(catalog.resolve("perception", "shared"), perception)
        self.assertEqual(catalog.resolve("memory", "shared"), memory)
        self.assertEqual(catalog.list("perception"), (perception,))
        self.assertEqual(catalog.list("memory"), (memory,))
        with self.assertRaisesRegex(PluginManagementError, "for step 'proposal'"):
            catalog.resolve("proposal", "shared")


class PluginManagerTests(unittest.TestCase):
    def test_select_add_and_remove_share_one_unbounded_collection(self) -> None:
        definitions = [
            PluginDefinition(
                step="perception",
                plugin_id=f"plugin_{index}",
                entrypoint=f"tests.fixtures.plugins:Plugin{index}",
            )
            for index in range(24)
        ]
        catalog = LocalPluginCatalog(definitions)
        manager = PluginManager("perception", catalog)

        selected = manager.select([f"plugin_{index}" for index in range(24)])
        self.assertEqual(len(selected), 24)
        self.assertEqual(manager.selected_ids, tuple(f"plugin_{i}" for i in range(24)))

        catalog.register(
            PluginDefinition(
                step="perception",
                plugin_id="plugin_24",
                entrypoint="tests.fixtures.plugins:Plugin24",
            )
        )
        manager.add("plugin_24")
        self.assertEqual(len(manager.selected), 25)

        manager.remove("plugin_0")
        self.assertNotIn("plugin_0", manager.selected_ids)
        self.assertEqual(len(manager.selected), 24)

    def test_failed_selection_leaves_current_selection_unchanged(self) -> None:
        first = PluginDefinition(
            step="memory",
            plugin_id="first",
            entrypoint="tests.fixtures.memory:First",
        )
        catalog = LocalPluginCatalog([first])
        manager = PluginManager("memory", catalog)
        manager.select(["first"])

        with self.assertRaisesRegex(PluginManagementError, "unknown plugin id"):
            manager.select(["first", "missing"])

        self.assertEqual(manager.selected, (first,))

    def test_selection_rejects_a_definition_owned_by_another_step(self) -> None:
        manager = PluginManager("proposal", LocalPluginCatalog())
        wrong_step = PluginDefinition(
            step="memory",
            plugin_id="bounded_evidence",
            entrypoint=(
                "implementations.memory.bounded_evidence:BoundedEvidenceLedger"
            ),
        )

        with self.assertRaisesRegex(PluginManagementError, "belongs to 'memory'"):
            manager.select([wrong_step])


class StepExecutableReferenceContractTests(unittest.TestCase):
    """Show that current step implementations fit the shared reference shape.

    These definitions exercise the proposed core contract. Step catalogs and
    runtime adapters have not yet been migrated to consume PluginManager.
    """

    def test_perception_memory_and_proposal_references_resolve_through_core(self) -> None:
        memory = memory_implementation_spec("bounded_evidence")
        definitions_and_targets = (
            (
                PluginDefinition(
                    step="perception",
                    plugin_id="frame",
                    entrypoint=PERCEPTION_PLUGIN_SPECS["frame"],
                ),
                FrameObservationPlugin,
            ),
            (
                PluginDefinition(
                    step="memory",
                    plugin_id="bounded_evidence",
                    entrypoint=memory["implementation_spec"],
                    config=memory["default_config"],
                ),
                BoundedEvidenceLedger,
            ),
            (
                PluginDefinition(
                    step="proposal",
                    plugin_id=PROPOSAL_PLUGIN_ID,
                    entrypoint=(
                        f"{avoid_recent_obstruction.__module__}:"
                        f"{avoid_recent_obstruction.__name__}"
                    ),
                    config={
                        "accepted_kinds": list(DEFAULT_ACCEPTED_KINDS),
                        "retained_max_age_ms": DEFAULT_RETAINED_MAX_AGE_MS,
                        "steer_magnitude": DEFAULT_STEER_MAGNITUDE,
                    },
                ),
                avoid_recent_obstruction,
            ),
        )
        catalog = LocalPluginCatalog(
            definition for definition, _ in definitions_and_targets
        )

        for definition, expected_target in definitions_and_targets:
            with self.subTest(step=definition.step, plugin_id=definition.plugin_id):
                manager = PluginManager(definition.step, catalog)
                selected = manager.select([definition.plugin_id])
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0].step, definition.step)
                self.assertEqual(selected[0].plugin_id, definition.plugin_id)
                self.assertEqual(selected[0].config, definition.config)
                self.assertIs(
                    _load_entrypoint(selected[0].entrypoint), expected_target
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
