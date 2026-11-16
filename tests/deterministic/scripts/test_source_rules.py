from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qca.evaluation import Context
from scripts.validation import review, source_rules
from tests.deterministic.scripts.test_inspection import inspect


class SourceRulesTests(unittest.TestCase):
    def test_object_cast_is_banned_including_typing_aliases_but_narrowing_cast_is_allowed(self):
        found = inspect(source_rules.object_casts, {"cli/value.py": (
            "from typing import cast as widen\nimport typing as t\n"
            "first = widen(object, value)\nsecond = t.cast(object, value)\n"
            "valid = widen(str, value)\nother = obj.cast(object, value)\n"
        )})
        self.assertEqual([item["location"]["line"] for item in found], [3, 4])
        self.assertTrue(all(item["status"] == "blocking" and "object-typed input" in item["question"] for item in found))

    def test_new_cross_module_copy_blocks_but_existing_same_module_tests_and_justified_copies_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, head = root / "base", root / "head"
            body = "    return value + 3\n"
            old = "def original(value):\n" + body
            files = {
                "cli/original.py": old + "\ndef neighbor(value):\n" + body,
                "cli/copy.py": "def copied(value):\n" + body,
                "cli/different.py": "def different(value):\n    return value + 4\n",
                "cli/justified.py": "def justified(value):  # quality: allow-identical-body -- protocol versions evolve independently\n" + body,
                "tests/test_copy.py": "def test_copy(value):\n" + body,
            }
            for tree in (base, head):
                (tree / "cli").mkdir(parents=True)
                (tree / "cli/original.py").write_text(files["cli/original.py"])
            for name, text in files.items():
                (head / name).parent.mkdir(parents=True, exist_ok=True)
                (head / name).write_text(text)
            context = Context("change", head, tuple(files), base=base)
            found = source_rules.identical_copies(context, ("cli", "tests"))
            self.assertEqual([item["location"]["path"] for item in found], ["cli/copy.py"])
            self.assertIn(source_rules.COPY_MARKER, found[0]["question"])
            # Empty marker cannot suppress a finding.
            (head / "cli/copy.py").write_text("def copied(value):  # quality: allow-identical-body --\n" + body)
            self.assertEqual(len(source_rules.identical_copies(context, ("cli",))), 1)


class ChangeReviewTests(unittest.TestCase):
    def test_configured_lifetime_paths_and_complexity_extraction_ask_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, head = root / "base", root / "head"
            for tree in (base, head):
                (tree / "cli").mkdir(parents=True)
            (head / "pyproject.toml").write_text('[tool.validation]\nlifecycle_paths = ["cli/owner.py"]\n')
            large = "def choose(value):\n" + "".join(f"    if value == {i}:\n        return {i}\n" for i in range(12)) + "    return -1\n"
            (base / "cli/owner.py").write_text(large)
            (head / "cli/owner.py").write_text("def choose(value):\n    return part(value)\n\ndef part(value):\n    return value\n")
            (head / "cli/unrelated.py").write_text("value = 1\n")
            context = Context("change", head, ("cli/owner.py", "cli/unrelated.py"), base=base)
            found = review.change_questions(context)
            self.assertEqual([item["rule"] for item in found], ["state-lifetime", "complexity-extraction"])
            self.assertTrue(all(item["status"] == "review" for item in found))
            self.assertEqual(found[1]["evidence"]["new_functions"], ["part"])
            self.assertEqual(review.complexity_totals(head, context.paths), {"cli/owner.py": 2, "cli/unrelated.py": 0})
            # Moving code without removing the complexity finding does not fire extraction advice.
            (head / "cli/owner.py").write_text(large + "\ndef part(value):\n    return value\n")
            self.assertEqual([item["rule"] for item in review.change_questions(context)], ["state-lifetime"])
