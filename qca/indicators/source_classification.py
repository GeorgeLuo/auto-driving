"""Identify test sources without confusing production modules named test_*."""

import ast
import re
from pathlib import PurePosixPath

TEST_DIRECTORIES = frozenset({"test", "tests", "spec"})
TEST_NAME = re.compile(r"test_.*\.pyi?|.*_test\.pyi?")


def is_test_source(path: str, text: str) -> bool:
    parts = PurePosixPath(path.replace("\\", "/").lower()).parts
    if TEST_DIRECTORIES.intersection(parts[:-1]):
        return True
    if not TEST_NAME.fullmatch(parts[-1] if parts else ""):
        return False
    return _has_test_evidence(text)  # A production module may be named like a test; its contents decide.


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
