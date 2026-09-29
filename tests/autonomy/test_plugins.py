from __future__ import annotations

import unittest
from typing import Any

from autonomy.plugins import (
    LocalPluginCatalog,
    PluginDefinition,
    PluginManagementError,
    PluginManager,
    PluginSelectionRuntime,
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

    def test_load_failure_does_not_reset_applied_instances(self) -> None:
        current = _definition("current")
        added = _definition("added")
        existing = object()
        resets: list[object] = []

        def fail_load(_definition: PluginDefinition) -> object:
            raise RuntimeError("missing entrypoint")

        with self.assertRaisesRegex(RuntimeError, "missing entrypoint"):
            replace_selection(
                ((current, existing),),
                (current, added),
                load=fail_load,
                reset=resets.append,
            )

        self.assertEqual(resets, [])

    def test_validate_failure_does_not_reset_removed_instances(self) -> None:
        current = _definition("current")
        replacement = _definition("replacement")
        old = object()
        loaded: list[str] = []
        resets: list[object] = []

        def load(definition: PluginDefinition) -> str:
            loaded.append(definition.plugin_id)
            return definition.plugin_id

        def reject(_instances: tuple[str, ...]) -> None:
            raise ValueError("provider conflict")

        with self.assertRaisesRegex(ValueError, "provider conflict"):
            replace_selection(
                ((current, old),),
                (replacement,),
                load=load,
                validate=reject,
                reset=resets.append,
            )

        self.assertEqual(loaded, ["replacement"])
        self.assertEqual(resets, [])

    def test_conflicting_definitions_are_rejected_before_load(self) -> None:
        loaded: list[str] = []

        with self.assertRaisesRegex(PluginManagementError, "conflicting definitions"):
            replace_selection(
                (),
                (
                    _definition("bounded", config={"max_records": 32}),
                    _definition("bounded", config={"max_records": 8}),
                ),
                load=lambda definition: loaded.append(definition.plugin_id),
            )

        self.assertEqual(loaded, [])

    def test_malformed_inputs_are_rejected(self) -> None:
        definition = _definition("bounded")
        with self.assertRaisesRegex(PluginManagementError, "PluginDefinition, instance"):
            replace_selection((definition,), (), load=lambda _definition: object())
        with self.assertRaisesRegex(PluginManagementError, "ordered collection"):
            replace_selection(frozenset(), (), load=lambda _definition: object())
        with self.assertRaisesRegex(PluginManagementError, "load must be callable"):
            replace_selection((), (), load=object())
        with self.assertRaisesRegex(PluginManagementError, "duplicate id"):
            replace_selection(
                ((definition, object()), (definition, object())),
                (),
                load=lambda _definition: object(),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
