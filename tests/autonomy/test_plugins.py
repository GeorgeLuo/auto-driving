from __future__ import annotations

import unittest
from typing import Any

from autonomy.plugins import (
    LocalPluginCatalog,
    PluginDefinition,
    PluginManager,
    PluginSelectionRuntime,
    plugin_report,
    replace_selection,
)


def _definition(plugin_id: str, **overrides: Any) -> PluginDefinition:
    payload: dict[str, Any] = {
        "step": "memory",
        "plugin_id": plugin_id,
        "entrypoint": f"implementations.example:{plugin_id}",
        "config": {},
    }
    payload.update(overrides)
    return PluginDefinition(**payload)


class PluginCatalogTests(unittest.TestCase):
    def test_availability_tracks_resolver_without_loading_or_selecting_plugins(self) -> None:
        first = _definition("first")
        second = _definition("second", config={"max_records": 8})
        resolver = LocalPluginCatalog((first, second, _definition("other", step="perception")))
        manager = PluginManager("memory", resolver)
        self.assertEqual(manager.available, (first, second))
        self.assertEqual(manager.selected_ids, ())
        manager.select(["first"])
        self.assertEqual(manager.available_ids, ("first", "second"))
        manager.remove("first")
        self.assertEqual(manager.available_ids, ("first", "second"))
        third = _definition("third")
        resolver.register(third)
        self.assertEqual(manager.available, (first, second, third))
        manager.add("third")
        self.assertEqual(manager.selected, (third,))


class ReplaceSelectionTests(unittest.TestCase):
    def test_equal_definitions_reuse_instances_without_callbacks(self) -> None:
        original = _definition("bounded", config={"max_records": 32})
        incoming = _definition("bounded", config={"max_records": 32})
        instance = object()
        calls: list[str] = []

        result = replace_selection(
            ((original, instance),),
            (incoming,),
            load=lambda _definition: calls.append("load"),
            validate=lambda _instances: calls.append("validate"),
            reset=lambda _instance: calls.append("reset"),
        )

        self.assertEqual(result, (instance,))
        self.assertEqual(calls, [])

    def test_duplicate_equal_definitions_collapse_to_current_instances(self) -> None:
        definition = _definition("bounded")
        instance = object()
        calls: list[str] = []

        result = replace_selection(
            ((definition, instance),),
            (definition, definition),
            load=lambda _definition: calls.append("load"),
            validate=lambda _instances: calls.append("validate"),
            reset=lambda _instance: calls.append("reset"),
        )

        self.assertEqual(result, (instance,))
        self.assertEqual(calls, [])

    def test_reorder_reuses_instances_and_validates_before_any_reset(self) -> None:
        first = _definition("first")
        second = _definition("second")
        first_instance = object()
        second_instance = object()
        calls: list[tuple[object, ...]] = []

        result = replace_selection(
            ((first, first_instance), (second, second_instance)),
            (second, first),
            load=lambda _definition: calls.append(("load",)),
            validate=lambda instances: calls.append(("validate", instances)),
            reset=lambda instance: calls.append(("reset", instance)),
        )

        self.assertEqual(result, (second_instance, first_instance))
        self.assertEqual(calls, [("validate", (second_instance, first_instance))])

    def test_added_definition_loads_without_resetting_the_existing_instance(self) -> None:
        current = _definition("current")
        added = _definition("added")
        existing = object()
        created = object()
        resets: list[object] = []

        result = replace_selection(
            ((current, existing),),
            (current, added),
            load=lambda definition: created if definition == added else object(),
            reset=resets.append,
        )

        self.assertEqual(result, (existing, created))
        self.assertEqual(resets, [])

    def test_changed_definition_reloads_and_resets_the_previous_instance(self) -> None:
        previous = _definition("bounded", config={"max_records": 32})
        updated = _definition("bounded", config={"max_records": 8})
        old = object()
        new = object()
        resets: list[object] = []

        result = replace_selection(
            ((previous, old),),
            (updated,),
            load=lambda _definition: new,
            reset=resets.append,
        )

        self.assertEqual(result, (new,))
        self.assertEqual(resets, [old])

    def test_removed_instances_reset_in_applied_order_after_validation(self) -> None:
        first = _definition("first")
        kept = _definition("kept")
        last = _definition("last")
        instances = {name: object() for name in ("first", "kept", "last")}
        events: list[tuple[str, object]] = []

        result = replace_selection(
            (
                (first, instances["first"]),
                (kept, instances["kept"]),
                (last, instances["last"]),
            ),
            (kept,),
            load=lambda definition: (_ for _ in ()).throw(
                AssertionError(f"unexpected load of {definition.plugin_id}")
            ),
            validate=lambda candidates: events.append(("validate", candidates)),
            reset=lambda instance: events.append(("reset", instance)),
        )

        self.assertEqual(result, (instances["kept"],))
        self.assertEqual(
            events,
            [
                ("validate", (instances["kept"],)),
                ("reset", instances["first"]),
                ("reset", instances["last"]),
            ],
        )


class PluginSelectionRuntimeTests(unittest.TestCase):
    def test_applies_multiple_manager_selections_and_reconciles_on_next_call(self) -> None:
        definitions = tuple(_definition(plugin_id) for plugin_id in ("first", "second", "third"))
        manager = PluginManager.from_specs(
            "perception",
            {definition.plugin_id: definition.entrypoint for definition in definitions},
            {"second": {"mode": "configured"}},
        )
        self.assertEqual(manager.selected, ())
        manager.select(("first", "second"))
        self.assertEqual(manager.selected[1].config, {"mode": "configured"})
        runtime = PluginSelectionRuntime(manager)
        loaded: dict[str, object] = {}
        reset: list[object] = []

        def load(definition: PluginDefinition) -> object:
            instance = object()
            loaded[definition.plugin_id] = instance
            return instance

        first_application = runtime.apply(load=load, reset=reset.append)
        self.assertEqual(
            tuple(definition.plugin_id for definition, _instance in first_application),
            ("first", "second"),
        )
        self.assertEqual(len(first_application), 2)

        manager.select(("second", "third"))
        second_application = runtime.apply(load=load, reset=reset.append)

        self.assertEqual(
            tuple(definition.plugin_id for definition, _instance in second_application),
            ("second", "third"),
        )
        self.assertIs(second_application[0][1], first_application[1][1])
        self.assertIs(second_application[1][1], loaded["third"])
        self.assertEqual(reset, [first_application[0][1]])
        self.assertEqual(runtime.applied, second_application)

    def test_failed_validation_keeps_the_previous_applied_selection(self) -> None:
        first = _definition("first")
        second = _definition("second")
        manager = PluginManager.from_specs(
            "perception",
            {definition.plugin_id: definition.entrypoint for definition in (first, second)},
        )
        manager.select(("first",))
        runtime = PluginSelectionRuntime(manager)
        original = runtime.apply(load=lambda _definition: object())
        manager.select(("second",))

        def reject(_instances: tuple[object, ...]) -> None:
            raise ValueError("step-specific validation failed")

        with self.assertRaisesRegex(ValueError, "step-specific validation failed"):
            runtime.apply(load=lambda _definition: object(), validate=reject)

        self.assertEqual(runtime.applied, original)
        self.assertEqual(manager.selected_ids, ("second",))

    def test_prepare_loads_and_validates_without_reset(self) -> None:
        manager = PluginManager.from_specs(
            "perception",
            {"kept": "implementations.example:kept", "added": "implementations.example:added"},
        )
        manager.select(("kept",))
        runtime = PluginSelectionRuntime(manager)
        kept = object()
        added = object()
        events: list[tuple[Any, ...]] = []

        def load(definition: PluginDefinition) -> object:
            events.append(("load", definition.plugin_id))
            return kept if definition.plugin_id == "kept" else added

        runtime.apply(load=load, reset=lambda instance: events.append(("reset", instance)))
        manager.select(("added", "kept"))
        prepared = runtime.prepare(
            load=load,
            validate=lambda instances: events.append(("validate", instances)),
        )

        self.assertEqual(
            events,
            [("load", "kept"), ("load", "added"), ("validate", (added, kept))],
        )
        self.assertIs(prepared[0][1], added)
        self.assertIs(prepared[1][1], kept)
        self.assertIs(runtime.applied[0][1], kept)
        self.assertEqual(
            tuple(definition.plugin_id for definition, _instance in runtime.applied),
            ("kept",),
        )

    def test_commit_resets_the_prepared_instances_without_loading_again(self) -> None:
        manager = PluginManager.from_specs(
            "perception",
            {
                "kept": "implementations.example:kept",
                "added": "implementations.example:added",
                "later": "implementations.example:later",
            },
        )
        manager.select(("kept",))
        runtime = PluginSelectionRuntime(manager)
        instances = {"kept": object(), "added": object(), "later": object()}
        events: list[tuple[Any, ...]] = []

        def load(definition: PluginDefinition) -> object:
            events.append(("load", definition.plugin_id))
            return instances[definition.plugin_id]

        runtime.apply(load=load)
        manager.select(("added",))
        prepared = runtime.prepare(
            load=load,
            validate=lambda candidates: events.append(("validate", candidates)),
        )
        manager.select(("later",))
        committed = runtime.commit(reset=lambda instance: events.append(("reset", instance)))

        self.assertEqual(
            events,
            [
                ("load", "kept"),
                ("load", "added"),
                ("validate", (instances["added"],)),
                ("reset", instances["kept"]),
            ],
        )
        self.assertEqual(committed, prepared)
        self.assertIs(runtime.applied[0][1], instances["added"])
        self.assertEqual(manager.selected_ids, ("later",))


class PluginReportTests(unittest.TestCase):
    def test_report_separates_selected_from_applied_records(self) -> None:
        manager = PluginManager.from_specs(
            "memory",
            {
                "first": "implementations.example:first",
                "second": "implementations.example:second",
                "third": "implementations.example:third",
            },
        )
        manager.select(("second", "first"))
        runtime = PluginSelectionRuntime(manager)
        runtime.apply(load=lambda definition: definition.plugin_id)
        manager.select(("third", "first"))

        report = plugin_report(
            manager,
            runtime.applied,
            [
                {
                    "plugin_id": "second",
                    "duration_ms": 1.5,
                    "error": None,
                    "bounds": {"max_records": 4},
                    "last_health": "healthy",
                    "status": "ok",
                },
                {
                    "plugin_id": "third",
                    "duration_ms": 9,
                    "error": "should not appear",
                },
                {
                    "plugin_id": "first",
                    "duration_ms": 0,
                    "error": "kept",
                },
            ],
        )

        self.assertEqual(report["available_plugin_ids"], ["first", "second", "third"])
        self.assertEqual(report["selected_plugin_ids"], ["third", "first"])
        self.assertEqual(report["applied_plugin_ids"], ["second", "first"])
        self.assertEqual(
            report["plugins"],
            [
                {
                    "plugin_id": "second",
                    "duration_ms": 1.5,
                    "error": None,
                },
                {
                    "plugin_id": "first",
                    "duration_ms": 0,
                    "error": "kept",
                },
            ],
        )
        self.assertEqual(
            set(report["plugins"][0]),
            {"plugin_id", "duration_ms", "error"},
        )

        blank = plugin_report(manager, runtime.applied)
        self.assertEqual(blank["selected_plugin_ids"], ["third", "first"])
        self.assertEqual(blank["applied_plugin_ids"], ["second", "first"])
        self.assertEqual(
            blank["plugins"],
            [
                {
                    "plugin_id": "second",
                    "duration_ms": None,
                    "error": None,
                },
                {
                    "plugin_id": "first",
                    "duration_ms": None,
                    "error": None,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
