from __future__ import annotations

import unittest
from typing import Any

from autonomy.plugins import (
    DuplicatePluginIdError,
    LocalPluginCatalog,
    PluginDefinition,
    PluginManagementError,
    declared_plugin_id,
    plugin_report,
    uploaded_source,
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


class _Declared:
    plugin_id = "declared"


class _Undeclared:
    pass


class PluginCatalogTests(unittest.TestCase):
    def test_a_second_definition_under_one_id_is_refused(self) -> None:
        resolver = LocalPluginCatalog((_definition("first"),))
        for duplicate in (_definition("first"), _definition("first", entrypoint="other:Plugin")):
            with self.subTest(entrypoint=duplicate.entrypoint):
                with self.assertRaisesRegex(DuplicatePluginIdError, "duplicate memory plugin id 'first'"):
                    resolver.register(duplicate)
        resolver.register(_definition("first", step="perception"))

    def test_declared_id_is_read_from_the_plugin_class(self) -> None:
        self.assertEqual(declared_plugin_id(f"{__name__}:_Declared"), "declared")
        with self.assertRaisesRegex(PluginManagementError, "does not declare a plugin_id"):
            declared_plugin_id(f"{__name__}:_Undeclared")
        definition = PluginDefinition.declared("memory", f"{__name__}:_Declared", {"a": 1})
        self.assertEqual((definition.plugin_id, dict(definition.config)), ("declared", {"a": 1}))


class PluginReportTests(unittest.TestCase):
    def test_report_describes_applied_plugins_only(self) -> None:
        applied = (
            (_definition("second"), object()),
            (_definition("first"), object()),
        )
        report = plugin_report(
            applied,
            [
                {"plugin_id": "second", "duration_ms": 1.5, "error": None, "bounds": {"max_records": 4}},
                {"plugin_id": "third", "duration_ms": 9, "error": "should not appear"},
                {"plugin_id": "first", "duration_ms": 0, "error": "kept"},
            ],
        )
        self.assertEqual(report["applied_plugin_ids"], ["second", "first"])
        self.assertNotIn("available_plugin_ids", report)
        self.assertNotIn("selected_plugin_ids", report)
        self.assertEqual(
            report["plugins"],
            [
                {"plugin_id": "second", "duration_ms": 1.5, "error": None},
                {"plugin_id": "first", "duration_ms": 0, "error": "kept"},
            ],
        )
        blank = plugin_report(applied)
        self.assertEqual(blank["applied_plugin_ids"], ["second", "first"])
        self.assertEqual(blank["plugins"][0]["duration_ms"], None)


def _source(revision: int) -> bytes:
    return f"class Plugin:\n    plugin_id = 'revision-{revision}'\n".encode()


class UploadedSourceTests(unittest.TestCase):
    def test_source_that_does_not_compile_is_refused_with_its_line(self) -> None:
        catalog = LocalPluginCatalog()
        with self.assertRaisesRegex(ValueError, r"^broken\.py:2: "):
            catalog.upload(
                step="proposal", plugin_id="broken", entrypoint="broken:Plugin",
                source=b"class Plugin:\n    def (\n", filename="broken.py",
            )
        self.assertEqual(catalog.read(), (0, ()))

    def test_each_revision_keeps_its_source_and_imports_by_content(self) -> None:
        catalog = LocalPluginCatalog()

        def upload(revision: int) -> PluginDefinition:
            return catalog.upload(
                step="proposal", plugin_id="uploaded", entrypoint="prototype:Plugin", source=_source(revision),
            )

        first = upload(1)
        again = upload(1)
        second = upload(2)
        self.assertEqual(again.metadata, first.metadata)
        self.assertNotEqual(second.metadata["source_file"], first.metadata["source_file"])
        self.assertEqual(catalog.version, 3)
        self.assertEqual(catalog.resolve("proposal", "uploaded"), second)
        # A selection that still names the first revision keeps running its code.
        self.assertEqual(uploaded_source(first.metadata["source_file"]).read_bytes(), _source(1))
        self.assertEqual(declared_plugin_id(first.executable_entrypoint), "revision-1")
        self.assertEqual(declared_plugin_id(second.executable_entrypoint), "revision-2")

    def test_a_failed_import_leaves_no_partial_module(self) -> None:
        catalog = LocalPluginCatalog()
        definition = catalog.upload(
            step="proposal", plugin_id="failing", entrypoint="failing:Plugin",
            source=b"raise RuntimeError('import failed')\nclass Plugin:\n    plugin_id = 'failing'\n",
        )
        for attempt in (1, 2):
            with self.subTest(attempt=attempt), self.assertRaisesRegex(RuntimeError, "import failed"):
                declared_plugin_id(definition.executable_entrypoint)

    def test_an_unknown_source_or_unnamed_plugin_is_refused(self) -> None:
        with self.assertRaisesRegex(PluginManagementError, "is not available here"):
            uploaded_source(f"{'0' * 64}.py")
        with self.assertRaisesRegex(PluginManagementError, "plugin plugin_id must be a non-empty string"):
            _definition(" ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
