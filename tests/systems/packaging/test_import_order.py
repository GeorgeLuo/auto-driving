"""Every module imports first in a fresh interpreter.

This catches import cycles that only appear for one entry order.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


_SCRIPT = """
import importlib
import sys
from pathlib import Path

PREFIXES = ("autonomy", "implementations")
modules = sorted(
    ".".join(path.with_suffix("").parts).removesuffix(".__init__")
    for prefix in PREFIXES
    for path in Path(prefix).rglob("*.py")
    if "__pycache__" not in path.parts
)
failures = []
for first in modules:
    for name in [name for name in sys.modules if name.startswith(PREFIXES)]:
        del sys.modules[name]
    try:
        importlib.import_module(first)
    except Exception as exc:
        failures.append(f"{first}: {type(exc).__name__}: {exc}")
print("\\n".join(failures))
sys.exit(1 if failures else 0)
"""


class ImportOrderTests(unittest.TestCase):
    def test_each_module_imports_first(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-c", _SCRIPT],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
