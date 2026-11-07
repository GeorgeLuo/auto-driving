from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validation import qca_summary


class QcaQualitySummaryTests(unittest.TestCase):
    def test_committed_change_produces_summary_and_complete_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args: str) -> str:
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

            git("init", "-q")
            git("config", "user.name", "Quality Test")
            git("config", "user.email", "quality@example.test")
            (root / "autonomy").mkdir()
            source = root / "autonomy" / "module.py"
            source.write_text("def choose(a, b, c):\n    return a\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            source.write_text("def choose(a, b, c):\n    if a and b and c:\n        return b\n    return a\n")
            git("add", ".")
            git("commit", "-qm", "head")
            head = git("rev-parse", "HEAD")
            output = root / "evidence"
            with contextlib.chdir(root), patch.object(qca_summary, "PATHS", (".",)), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(qca_summary.main(["--base", base, "--head", head, "--output", str(output)]), 0)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["identity"]["base_sha"], base)
            self.assertEqual(report["identity"]["head_sha"], head)
            self.assertEqual(report["factors"]["patterns"]["metrics"]["opaque_conditional_count"], 1)
            self.assertIn("autonomy/module.py:2", (output / "summary.md").read_text())
            self.assertIn("<!doctype html>", (output / "report.html").read_text().lower())


if __name__ == "__main__":
    unittest.main()
