from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validation import check, evaluators

ROOT = Path(__file__).resolve().parents[3]
PERCEPTION = "implementations.decision_cycle.perception"
# Modules the configured import contracts name; import-linter rejects a contract that matches nothing.
CONTRACT_MODULES = (
    "autonomy.core", "implementations.runtime.host", "implementations.vehicle.car",
    f"{PERCEPTION}.shared.math", f"{PERCEPTION}.bridge",
    f"{PERCEPTION}.plugins.first.plugin", f"{PERCEPTION}.plugins.second.plugin", "cli.app",
)


def module_files(module: str, source: str = "", package: str = "") -> dict[str, str]:
    """A module and the ``__init__.py`` of each package above it."""

    parts = module.split(".")
    files = {"/".join([*parts[:depth], "__init__.py"]): package for depth in range(1, len(parts))}
    return {**files, "/".join(parts) + ".py": source}


def branches(count: int) -> str:
    return (
        "def choose(value: int) -> int:\n"
        + "".join(f"    if value == {i}:\n        return {i}\n" for i in range(count))
        + "    return -1\n"
    )


CALLER = (
    "from cli.values import parse\n\n\n"
    "def use() -> int:\n"
    "    value = parse('1')\n"
    "    if isinstance(value, int):\n"
    "        return value\n"
    "    return 0\n"
)


class QualityCommandTests(unittest.TestCase):
    """The configured Ruff and basedpyright, judged against a scratch repository's base revision."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Quality Test")
        self.git("config", "user.email", "quality@example.test")
        self.base = self.commit({
            **{name: text for module in CONTRACT_MODULES for name, text in module_files(module).items()},
            "pyproject.toml": (ROOT / "pyproject.toml").read_text(),
            "cli/__init__.py": "",
            "cli/choose.py": branches(12),
            "cli/values.py": "def parse(raw: str) -> int | str:\n    return int(raw) if raw.isdigit() else raw\n",
            "cli/caller.py": CALLER,
            "cli/unused.py": "import os\n",
        })

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def commit(self, files: dict[str, str]) -> str:
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", "change")
        return self.git("rev-parse", "HEAD")

    def check(self, *args: str, stdin: str = "") -> tuple[int, dict]:
        output = self.root.parent / f"{self.root.name}-quality"
        with (
            patch.object(check, "ROOT", self.root),
            patch("sys.stdin", io.StringIO(stdin)),
            contextlib.chdir(self.root),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            status = check.main([*args, "--output", str(output)])
        return status, json.loads((output / "findings.json").read_text())

    def blocking(self, document: dict) -> list[tuple[str, str, str]]:
        return sorted(
            (item["tool"], item["rule"], item["location"]["path"])
            for item in document["findings"] if item["status"] == "blocking"
        )

    def test_a_change_answers_for_growth_new_errors_and_callers_it_breaks(self) -> None:
        head = self.commit({
            "cli/choose.py": branches(13),
            "cli/values.py": "def parse(raw: str) -> int:\n    return int(raw)\n",
            "cli/new.py": "def broken() -> int:\n    return missing\n",
        })
        status, document = self.check("--base", self.base, "--head", head)
        self.assertEqual(status, 1)
        self.assertEqual(self.blocking(document), [
            ("basedpyright", "reportUnnecessaryIsInstance", "cli/caller.py"),
            ("ruff", "C901", "cli/choose.py"),
            ("ruff", "F821", "cli/new.py"),
            ("ruff", "PLR0912", "cli/choose.py"),
        ])

    def test_a_changed_file_reports_a_smaller_function_still_above_the_limit(self) -> None:
        head = self.commit({"cli/choose.py": branches(11)})
        status, document = self.check("--base", self.base, "--head", head)
        self.assertEqual(status, 1, document["findings"])
        self.assertEqual(self.blocking(document), [("ruff", "C901", "cli/choose.py")])
        self.assertTrue(all(record["status"] == "completed" for record in document["evaluations"]))

    def test_a_change_answers_for_an_import_that_breaks_a_contract(self) -> None:
        head = self.commit(module_files("autonomy.core", "from cli import app\n\nCOMMANDS = app\n"))
        status, document = self.check("--base", self.base, "--head", head)
        self.assertEqual(status, 1)
        self.assertEqual(self.blocking(document), [("import-linter", "harness", "autonomy/core.py")])

    def test_whole_files_report_their_existing_debt(self) -> None:
        status, document = self.check("cli/unused.py", "cli/choose.py")
        self.assertEqual(status, 1)
        self.assertEqual(document["context"]["form"], "files")
        self.assertEqual(self.blocking(document), [("ruff", "C901", "cli/choose.py"), ("ruff", "F401", "cli/unused.py")])

    def test_text_is_checked_as_the_file_it_names(self) -> None:
        status, document = self.check("--text", "cli/snippet.py", stdin="def broken():\n    return missing\n")
        self.assertEqual(status, 1)
        self.assertEqual(self.blocking(document), [("ruff", "F821", "cli/snippet.py")])
        self.assertEqual(
            next(record for record in document["evaluations"] if record["evaluator"] == "basedpyright")["status"],
            "skipped",
        )



class ImportContractTests(unittest.TestCase):
    """The configured import contracts, checked against small packages."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
        for module in CONTRACT_MODULES:
            self.write(module)

    def write(self, module: str, source: str = "") -> None:
        # Checking the graph must not execute application modules.
        for name, text in module_files(module, source, "raise RuntimeError('application code executed')\n").items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)

    def broken(self) -> list[tuple[str, str, int, list[str]]]:
        return [
            (item["rule"], item["location"]["path"], item["location"]["line"], [place["path"] for place in item["related"]])
            for item in evaluators.import_contracts(self.root, [])
        ]

    def test_plugins_use_shared_code_and_specialize_one_way(self) -> None:
        self.write(f"{PERCEPTION}.shared.math", "import autonomy.core\n")
        self.write(f"{PERCEPTION}.plugins.first.plugin", f"from {PERCEPTION}.shared import math\n")
        self.write(f"{PERCEPTION}.plugins.second.plugin", "from ..first import plugin\n")
        self.write("cli.app", f"from {PERCEPTION}.plugins.first import plugin\n")
        self.assertEqual(self.broken(), [])

    def test_the_harness_cannot_import_an_implementation_even_for_type_checking(self) -> None:
        self.write("autonomy.core", "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import implementations.vehicle.car\n")
        self.assertEqual(self.broken(), [("harness", "autonomy/core.py", 3, [])])

    def test_an_implementation_cannot_import_the_cli(self) -> None:
        self.write("implementations.runtime.host", "\nfrom cli import app\n")
        self.assertEqual(self.broken(), [("implementations", "implementations/runtime/host.py", 2, [])])

    def test_shared_code_cannot_reach_a_plugin_through_another_module(self) -> None:
        self.write(f"{PERCEPTION}.shared.math", "from .. import bridge\n")
        self.write(f"{PERCEPTION}.bridge", "from .plugins.first import plugin\n")
        [finding] = evaluators.import_contracts(self.root, [])
        self.assertEqual(finding["location"]["path"], "implementations/decision_cycle/perception/shared/math.py")
        self.assertEqual([place["path"] for place in finding["related"]], ["implementations/decision_cycle/perception/bridge.py"])
        self.assertIn("guidance", finding["evidence"])

    def test_a_relative_aliased_plugin_import_from_shared_code_is_rejected(self) -> None:
        self.write(f"{PERCEPTION}.shared.math", "from ..plugins.first import plugin as reusable\n")
        self.assertEqual([rule for rule, *_ in self.broken()], ["shared"])

    def test_plugin_packages_cannot_import_each_other_in_a_cycle(self) -> None:
        self.write(f"{PERCEPTION}.plugins.first.plugin", "from ..second import plugin\n")
        self.write(f"{PERCEPTION}.plugins.second.plugin", "\nfrom ..first import plugin\n")
        rules = {rule for rule, *_ in self.broken()}
        self.assertEqual(rules, {"plugin-cycles"})


if __name__ == "__main__":
    unittest.main()
