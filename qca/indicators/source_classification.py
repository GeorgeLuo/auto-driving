"""Identify test sources without confusing production modules named test_*."""

import ast
from pathlib import PurePosixPath


def is_test_source(path: str, text: str) -> bool:
    parts = PurePosixPath(path.replace("\\", "/").lower()).parts
    if {"test", "tests", "spec"}.intersection(parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    if not name.endswith((".py", ".pyi")) or not (name.startswith("test_") or name.endswith(("_test.py", "_test.pyi"))):
        return False
    return _has_test_evidence(text)


def _has_test_evidence(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # A broken conventionally named test must remain visible as a test parse failure.
        return True
    for node in tree.body:
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] in {"pytest", "unittest"} for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {"pytest", "unittest"}:
            return True
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
               for node in ast.walk(tree))
