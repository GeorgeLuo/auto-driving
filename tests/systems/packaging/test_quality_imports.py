"""Exercise the actual configured dependency contracts with small packages."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validation import check


ROOT = Path(__file__).resolve().parents[3]
PERCEPTION = "implementations.decision_cycle.perception"


def write_module(root: Path, module: str, source: str = "") -> None:
    parts = module.split(".")
    for depth in range(1, len(parts)):
        package = root.joinpath(*parts[:depth])
        package.mkdir(exist_ok=True)
        # Graph inspection must not execute application modules.
        (package / "__init__.py").write_text("raise RuntimeError('application code executed')\n")
    root.joinpath(*parts).with_suffix(".py").write_text(source)


class ImportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
        for module in (
            "autonomy.core", "implementations.runtime.host", "implementations.vehicle.car",
            f"{PERCEPTION}.shared.math", f"{PERCEPTION}.bridge",
            f"{PERCEPTION}.plugins.first.plugin", f"{PERCEPTION}.plugins.second.plugin", "cli.app",
        ):
            write_module(self.root, module)

    def status(self) -> int:
        with patch.object(check, "ROOT", self.root):
            return check.check_imports()

    def test_plugin_to_shared_to_harness_and_one_way_specialization_are_allowed(self) -> None:
        write_module(self.root, f"{PERCEPTION}.shared.math", "import autonomy.core\n")
        write_module(self.root, f"{PERCEPTION}.plugins.first.plugin", f"from {PERCEPTION}.shared import math\n")
        write_module(self.root, f"{PERCEPTION}.plugins.second.plugin", "from ..first import plugin\n")
        write_module(self.root, "cli.app", f"from {PERCEPTION}.plugins.first import plugin\n")
        self.assertEqual(self.status(), 0)

    def test_harness_cannot_import_concrete_implementation_even_under_type_checking(self) -> None:
        write_module(self.root, "autonomy.core", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import implementations.vehicle.car\n")
        self.assertEqual(self.status(), 1)

    def test_shared_cannot_reach_plugin_through_an_intermediate_module(self) -> None:
        write_module(self.root, f"{PERCEPTION}.shared.math", "from .. import bridge\n")
        write_module(self.root, f"{PERCEPTION}.bridge", "from .plugins.first import plugin\n")
        records = []
        with patch.object(check, "ROOT", self.root):
            self.assertEqual(check.check_imports(records), 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["type"], "architecture.forbidden_dependency")
        self.assertEqual(records[0]["location"]["path"], "implementations/decision_cycle/perception/shared/math.py")
        self.assertEqual(records[0]["location"]["line"], 1)
        self.assertEqual(records[0]["related"][0]["path"], "implementations/decision_cycle/perception/bridge.py")

    def test_relative_aliased_plugin_import_from_shared_is_rejected(self) -> None:
        write_module(self.root, f"{PERCEPTION}.shared.math", "from ..plugins.first import plugin as reusable\n")
        self.assertEqual(self.status(), 1)

    def test_implementation_cannot_depend_on_cli(self) -> None:
        write_module(self.root, "implementations.runtime.host", "from cli import app\n")
        self.assertEqual(self.status(), 1)

    def test_plugin_package_cycles_are_rejected(self) -> None:
        write_module(self.root, f"{PERCEPTION}.plugins.first.plugin", "from ..second import plugin\n")
        write_module(self.root, f"{PERCEPTION}.plugins.second.plugin", "from ..first import plugin\n")
        records = []
        with patch.object(check, "ROOT", self.root):
            self.assertEqual(check.check_imports(records), 1)
        self.assertEqual(records[0]["type"], "architecture.dependency_cycle")
        self.assertEqual(records[0]["location"]["line"], 1)


if __name__ == "__main__":
    unittest.main()
