from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qca.evaluation import location, validate_finding
from qca.evaluators import structure

BRANCHY = "def choose(value):\n" + "".join(f"    if value == {i}:\n        return {i}\n" for i in range(11)) + "    return -1\n"
CLONE = "def {name}(items):\n    total = 0\n    for item in items:\n        if item > 2:\n            total += item * 3\n    return total\n"


class StructureEvaluatorTests(unittest.TestCase):
    def evaluate(self, files: dict[str, str]) -> list[dict]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            findings = structure(root, sorted(files))
        for item in findings:
            validate_finding(item)
        return findings

    def test_complex_callables_carry_the_measure_a_change_is_compared_by(self) -> None:
        [item] = self.evaluate({"cli/choose.py": BRANCHY})
        self.assertEqual(item["rule"], "structure.complexity.cyclomatic_complexity")
        self.assertEqual(item["location"], location("cli/choose.py", 1, 24, "choose"))
        self.assertEqual(item["measure"], {"value": 12, "limit": 10})

    def test_conditionals_and_clones_locate_their_code_and_related_copies(self) -> None:
        findings = self.evaluate({
            "cli/check.py": "class Gate:\n    def ready(self, a, b, c):\n        return a and b and c\n",
            "cli/one.py": CLONE.format(name="one"),
            "cli/two.py": CLONE.format(name="two"),
            "README.md": "not python\n",
        })
        by_rule = {item["rule"]: item for item in findings}
        conditional = by_rule["structure.opaque_conditionals.opaque_conditional"]
        self.assertEqual(conditional["location"], location("cli/check.py", 3, 3, "Gate.ready"))
        self.assertEqual(conditional["evidence"]["predicate_count"], 3)
        clone = by_rule["structure.redundancy.callable_clone"]
        self.assertEqual(clone["location"], location("cli/one.py", 1, 6, "one"))
        self.assertEqual(clone["related"], [location("cli/two.py", 1, 6, "two")])
        self.assertTrue(all(item["status"] == "review" for item in findings))


if __name__ == "__main__":
    unittest.main()
