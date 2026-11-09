from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validation import check

ROOT = Path(__file__).resolve().parents[3]


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

    def test_a_change_that_shrinks_existing_debt_passes(self) -> None:
        head = self.commit({"cli/choose.py": branches(11)})
        status, document = self.check("--base", self.base, "--head", head)
        self.assertEqual(status, 0, document["findings"])
        self.assertTrue(all(record["status"] == "completed" for record in document["evaluations"]))

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


if __name__ == "__main__":
    unittest.main()
