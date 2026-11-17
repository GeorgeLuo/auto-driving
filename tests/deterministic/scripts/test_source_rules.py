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

    def test_production_code_recovering_a_type_from_a_dict_or_its_own_encoding_is_asked_about(self):
        found = inspect(source_rules.recovered_types, {
            "cli/store.py": (
                "from collections.abc import Mapping\n"
                "from cli.models import Report\n"
                "def copy(report):\n"
                "    return Report.from_dict(report.to_dict())\n"
                "def read(cache, key):\n"
                "    value = cache.get(key)\n"
                "    if isinstance(value, Report):\n"
                "        return value\n"
                "    if isinstance(cache['raw'], (dict, Mapping)):\n"
                "        return None\n"
                "    for value in cache.values():\n"
                "        if isinstance(value, Report):\n"
                "            return value\n"
                "def parse(payload, expected_type):\n"
                "    state = Report.from_dict(payload)\n"
                "    assert state is not None\n"
                "    assert payload is not None\n"
                "    return isinstance(payload[0], Report) or isinstance(payload['x'], expected_type)\n"
            ),
            "tests/test_store.py": "def test_copy(report):\n    assert Report.from_dict(report.to_dict()) == report\n",
        })
        self.assertEqual([(item["location"]["path"], item["location"]["line"]) for item in found],
                         [("cli/store.py", 4), ("cli/store.py", 7), ("cli/store.py", 16)])
        self.assertTrue(all(item["status"] == "review" for item in found))

    def test_production_code_deciding_a_rule_autonomy_fields_owns_is_asked_about(self):
        found = inspect(source_rules.field_rules, {
            "cli/document.py": (
                "import math\n"
                "def parse(value, name):\n"
                "    if isinstance(value, bool) or not isinstance(value, int):\n"
                "        raise ValueError(value)\n"
                "    if type(value) is float and math.isfinite(value):\n"
                "        pass\n"
                "    if not isinstance(name, str) or not name.strip():\n"
                "        raise ValueError(name)\n"
                "    return isinstance(name, str) and name.startswith('x')\n"
                "def write(error, flag, items):\n"
                "    payload = {'kept': error}\n"
                "    if error is not None:\n"
                "        payload['error'] = error\n"
                "    if flag:\n"
                "        payload['bytes'] = b''\n"
                "    return {**payload, **({'items': items} if items else {}), 'pairs': {k: v for k, v in items if v is not None}}\n"
                "class Record:\n"
                "    @classmethod\n"
                "    def from_dict(cls, data):\n"
                "        return cls(data['name'], data.get('label'))\n"
                "def read(data):\n"
                "    return data.get('label')\n"
            ),
            "autonomy/fields.py": "def require_int(value):\n    return type(value) is int\n",
            "tests/test_document.py": "def test_parse(value):\n    assert type(value) is int\n",
        })
        self.assertEqual([(item["location"]["line"], item["rule"]) for item in found], [
            (3, "inline-field-rule"), (5, "inline-field-rule"), (7, "inline-field-rule"),
            (12, "omitted-key"), (16, "omitted-key"), (20, "optional-read"),
        ])
        self.assertTrue(all(item["status"] == "review" and item["location"]["path"] == "cli/document.py"
                            for item in found))

    def test_new_cross_module_copy_blocks_but_existing_same_module_stub_tests_and_justified_copies_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, head = root / "base", root / "head"
            body = "    total = value + 3\n    checked = check(total)\n    return checked\n"
            old = "def original(value):\n" + body + "\ndef stop(self):\n    ...\n"
            files = {
                "cli/original.py": old + "\ndef neighbor(value):\n" + body,
                "cli/renamed.py": "def renamed(value):\n" + body,
                "cli/copy.py": "def copied(value):\n" + body,
                "cli/stub.py": "def reset(self):\n    ...\n",
                "cli/different.py": "def different(value):\n" + body.replace("3", "4"),
                "cli/justified.py": "def justified(value):  # quality: allow-identical-body -- protocol versions evolve independently\n" + body,
                "tests/test_copy.py": "def test_copy(value):\n" + body,
            }
            for tree in (base, head):
                (tree / "cli").mkdir(parents=True)
                (tree / "cli/original.py").write_text(files["cli/original.py"])
            (base / "cli/renamed.py").write_text("def before(value):\n" + body)
            for name, text in files.items():
                (head / name).parent.mkdir(parents=True, exist_ok=True)
                (head / name).write_text(text)
            context = Context("change", head, tuple(files), base=base)
            found = source_rules.identical_copies(context, ("cli", "tests"))
            self.assertEqual([item["location"]["path"] for item in found], ["cli/copy.py"])
            self.assertIn(source_rules.COPY_MARKER, found[0]["question"])
            self.assertTrue(found[0]["evidence"]["introduced"])
            # Whole files report the copy without claiming the copy is new.
            [whole] = source_rules.identical_copies(Context("files", head, ("cli/copy.py",)), ("cli",))
            self.assertNotIn("introduced", whole["evidence"])
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
            (head / "cli/unrelated.py").write_text("value = isinstance(1, int)\n")
            context = Context("change", head, ("cli/owner.py", "cli/unrelated.py"), base=base)
            found = review.change_questions(context)
            self.assertEqual([item["rule"] for item in found], ["state-lifetime", "complexity-extraction"])
            self.assertTrue(all(item["status"] == "review" for item in found))
            self.assertEqual(found[1]["evidence"]["new_functions"], ["part"])
            self.assertEqual(review.complexity_totals(head, context.paths), {"cli/owner.py": 2, "cli/unrelated.py": 0})
            self.assertEqual(review.runtime_type_checks(head, context.paths), {"cli/owner.py": 0, "cli/unrelated.py": 1})
            # Moving code without removing the complexity finding does not fire extraction advice.
            (head / "cli/owner.py").write_text(large + "\ndef part(value):\n    return value\n")
            self.assertEqual([item["rule"] for item in review.change_questions(context)], ["state-lifetime"])
