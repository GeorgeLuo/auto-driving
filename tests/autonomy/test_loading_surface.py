"""Source paths stay within the loading inventory.

Scanning the tree does not rebuild the baseline: a catalog or export list can
stop mentioning an old path without dropping its check. The scan only reports
a spec or export that the inventory does not already record, which means a change reaches consumers outside it.
"""

from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

from tests.integration.controller_bundle.loading_inventory import (
    LEGACY_EXPORTS,
    LEGACY_MODULES,
    LEGACY_SPECS,
)

ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOTS = (
    ROOT / "autonomy",
    ROOT / "implementations",
    ROOT / "cli",
    ROOT / "scripts",
    ROOT / "deploy" / "targets" / "donkeycar" / "app",
)
_SPEC_PREFIXES = ("autonomy.", "implementations.", "cli.")
_MODULE_PREFIXES = ("autonomy.", "implementations.", "cli.")
_OUTSIDE_BASELINE = (
    "paths outside the migration baseline; this change reaches consumers "
    "the inventory does not pin"
)


def _python_files(roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "vendor" in path.parts:
                continue
            files.append(path)
    return files


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_import_spec(value: str) -> bool:
    module_name, separator, attribute = value.partition(":")
    if (
        not separator
        or ":" in attribute
        or not attribute.isidentifier()
        or not module_name.startswith(_SPEC_PREFIXES)
    ):
        return False
    return all(part.isidentifier() for part in module_name.split("."))


def _is_dotted_path(value: str) -> bool:
    if ":" in value or not value.startswith(_MODULE_PREFIXES):
        return False
    return all(part.isidentifier() for part in value.split("."))


def _string_constants(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((node.value, node.lineno))
    return found


def _static_exports(path: Path) -> list[str] | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = False
    names: list[str] | None = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        found = True
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple)) and all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in value.elts
        ):
            names = [element.value for element in value.elts]
        else:
            names = None
    if not found:
        return []
    return names


def _known_specs() -> set[str]:
    known: set[str] = set()
    for spec, module_name, attribute in LEGACY_SPECS:
        known.add(spec)
        known.add(f"{module_name}:{attribute}")
    return known


def _known_exports() -> set[tuple[str, str]]:
    known: set[tuple[str, str]] = set()
    for legacy_module, legacy_name, canonical_module, canonical_name in LEGACY_EXPORTS:
        known.add((legacy_module, legacy_name))
        known.add((canonical_module, canonical_name))
    return known


def _known_modules() -> set[str]:
    known: set[str] = set()
    for legacy_module, canonical_module in LEGACY_MODULES:
        known.add(legacy_module)
        known.add(canonical_module)
    return known


class LoadingSurfaceTests(unittest.TestCase):
    def test_changes_outside_the_baseline_are_visible(self) -> None:
        # Absence of a recorded path from the tree is not a failure. A new spec,
        # or export that the inventory does not
        # already name is a larger consumer surface than the baseline.
        known_specs = _known_specs()
        known_exports = _known_exports()
        known_modules = _known_modules()
        outside: list[str] = []

        for path in _python_files(_SOURCE_ROOTS):
            relative = path.relative_to(ROOT).as_posix()
            for value, lineno in _string_constants(path):
                location = f"{relative}:{lineno}"
                if _is_import_spec(value):
                    if value not in known_specs:
                        outside.append(f"{location} spec {value}")
                    continue
                if not _is_dotted_path(value):
                    continue
                try:
                    importlib.import_module(value)
                except ModuleNotFoundError:
                    module_name, separator, attribute = value.rpartition(".")
                    if not separator:
                        continue
                    try:
                        getattr(importlib.import_module(module_name), attribute)
                    except Exception:
                        continue
                    if (module_name, attribute) not in known_exports:
                        outside.append(f"{location} import {module_name}.{attribute}")
                else:
                    if value not in known_modules:
                        outside.append(f"{location} module {value}")

        for path in _python_files((ROOT / "autonomy", ROOT / "implementations")):
            relative = path.relative_to(ROOT).as_posix()
            exported = _static_exports(path)
            if exported is None:
                outside.append(f"{relative} __all__ is not a static name list")
                continue
            module_name = _module_name(path)
            for name in exported:
                if (module_name, name) not in known_exports:
                    outside.append(f"{relative} export {module_name}.{name}")

        self.assertEqual(outside, [], _OUTSIDE_BASELINE)


if __name__ == "__main__":
    unittest.main()
