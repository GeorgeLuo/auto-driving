"""Every module imports first in a fresh interpreter, and legacy paths still
resolve to their canonical owners afterward.

This catches import cycles that only appear for one entry order, and modules
loaded twice under different names. The loading inventory lists the paths.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_SCRIPT = """
import importlib
import sys
from pathlib import Path

from tests.integration.controller_bundle.loading_inventory import LEGACY_EXPORTS

PREFIXES = ("autonomy", "implementations")
modules = sorted(
    ".".join(path.with_suffix("").parts).removesuffix(".__init__")
    for prefix in PREFIXES
    for path in Path(prefix).rglob("*.py")
    if "__pycache__" not in path.parts
)
exports = [row for row in LEGACY_EXPORTS if row[0].startswith(PREFIXES)]
failures = []
for first in modules:
    for name in [name for name in sys.modules if name.startswith(PREFIXES)]:
        del sys.modules[name]
    try:
        importlib.import_module(first)
        for legacy_module, legacy_name, canonical_module, canonical_name in exports:
            legacy = getattr(importlib.import_module(legacy_module), legacy_name)
            canonical = getattr(importlib.import_module(canonical_module), canonical_name)
            if legacy is not canonical:
                failures.append(f"{first}: {legacy_module}.{legacy_name} is not the canonical owner")
                break
    except Exception as exc:
        failures.append(f"{first}: {type(exc).__name__}: {exc}")
print("\\n".join(failures))
sys.exit(1 if failures else 0)
"""


class ImportOrderTests(unittest.TestCase):
    def test_each_module_imports_first_and_legacy_paths_keep_identity(self) -> None:
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
