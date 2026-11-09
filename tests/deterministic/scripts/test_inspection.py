from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qca.evaluation import validate_finding
from scripts.validation.inspection import related, unused

STEPS = "    data = load(path)\n    checked = validate(data)\n    return publish(checked)\n"


def inspect(evaluator, files: dict[str, str]) -> list[dict]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        findings = evaluator(root, sorted({name.split("/")[0] for name in files}))
    for item in findings:
        validate_finding(item)
    return findings


class UnusedCodeTests(unittest.TestCase):
    def test_production_code_only_tests_use_is_located_at_its_definition(self) -> None:
        [item] = inspect(unused, {
            "cli/service.py": "def used():\n    return 1\n\n\ndef stale():\n    return used()\n",
            "cli/app.py": "from cli.service import used\n\nused()\n",
            "tests/test_service.py": "from cli.service import stale\n\n\ndef test_stale():\n    assert stale() == 1\n",
        })
        self.assertEqual((item["tool"], item["rule"], item["status"]), ("vulture", "unused-function", "review"))
        self.assertEqual((item["location"]["path"], item["location"]["line"], item["location"]["end_line"]), ("cli/service.py", 5, 5))
        self.assertEqual(item["evidence"]["test_references"], ["tests/test_service.py:5"])

    def test_attributes_and_decorated_methods_are_named_within_their_class(self) -> None:
        findings = inspect(unused, {"cli/model.py": (
            "class Model:\n    def __init__(self):\n        self.cache = {}\n\n"
            "    @property\n    def size(self):\n        return 0\n\n\nModel()\n"
        )})
        self.assertEqual(
            [(item["rule"], item["location"]["line"], item["location"]["symbol"]) for item in findings],
            [("unused-attribute", 3, "Model.cache"), ("unused-property", 5, "Model.size")],
        )


class RelatedFunctionTests(unittest.TestCase):
    def test_functions_sharing_calls_nearby_are_compared_whole(self) -> None:
        findings = inspect(related, {
            "cli/jobs.py": f"def run_job(path):\n{STEPS}\n\ndef rerun_job(path):\n{STEPS}",
            "cli/other/far.py": f"def elsewhere(path):\n{STEPS}",
        })
        located = {item["location"]["symbol"]: item for item in findings}
        self.assertEqual(sorted(located), ["rerun_job", "run_job"])
        self.assertEqual((located["run_job"]["location"]["line"], located["run_job"]["location"]["end_line"]), (1, 4))
        self.assertEqual([place["symbol"] for place in located["run_job"]["related"]], ["rerun_job"])
        self.assertEqual(located["run_job"]["evidence"]["matches"][0]["relationship"], "same module")

    def test_a_nested_function_is_not_compared_with_its_parent(self) -> None:
        nested = "def outer(path):\n    def inner(path):\n" + STEPS.replace("    ", "        ") + STEPS
        self.assertEqual(inspect(related, {"cli/nested.py": nested}), [])


if __name__ == "__main__":
    unittest.main()
