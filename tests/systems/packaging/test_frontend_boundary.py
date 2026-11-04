"""The runtime viewer under ``cli/automa_cli/frontend`` imports no other CLI module.

Automa passes the viewer its host list and replay runner, so the frontend can be
served on its own. An import of a CLI internal such as ``host_status`` breaks
that, and only a fresh interpreter shows which modules an import pulled in.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


_SCRIPT = """
import importlib
import pkgutil
import sys

import cli.automa_cli.frontend as frontend

for module in pkgutil.walk_packages(frontend.__path__, "cli.automa_cli.frontend."):
    importlib.import_module(module.name)
outside = sorted(
    name for name in sys.modules
    if name.startswith("cli.automa_cli.") and not name.startswith("cli.automa_cli.frontend")
)
print("\\n".join(outside))
sys.exit(1 if outside else 0)
"""


class FrontendBoundaryTests(unittest.TestCase):
    def test_frontend_modules_load_no_other_cli_module(self) -> None:
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
