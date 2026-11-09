from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qca.evaluation import (
    Context,
    Evaluator,
    evaluate,
    finding,
    introduced,
    location,
    text_context,
    validate_finding,
)


def undefined(path: str, line: int, tool: str = "lint") -> dict:
    return finding(tool, "F821", location(path, line), "Is this name defined?", status="blocking")


def too_complex(path: str, line: int, value: int) -> dict:
    return finding("lint", "C901", location(path, line), "Is this too complex?", measure={"value": value, "limit": 10})


def tree(directory: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text)
    return directory


def lines_with(word: str, tool: str = "lint"):
    """An evaluator reporting every line containing ``word`` in the files under its paths."""

    def run(root: Path, paths: list[str]) -> list[dict]:
        files = [file for path in paths for file in ([root / path] if (root / path).is_file() else (root / path).rglob("*.py"))]
        return [
            undefined(file.relative_to(root).as_posix(), number, tool)
            for file in sorted(files)
            for number, line in enumerate(file.read_text().splitlines(), start=1)
            if word in line
        ]

    return run


class FindingShapeTests(unittest.TestCase):
    def test_built_findings_are_valid(self) -> None:
        validate_finding(undefined("a.py", 2))
        validate_finding(too_complex("a.py", 1, 12))

    def test_rejects_shapes_consumers_cannot_read(self) -> None:
        cases = {
            "missing field": lambda item: item.pop("measure"),
            "extra field": lambda item: item.update(kind="lint"),
            "unknown status": lambda item: item.update(status="error"),
            "pathless location": lambda item: item["location"].pop("path"),
            "reversed lines": lambda item: item["location"].update(end_line=1, line=3),
            "invalid related location": lambda item: item["related"].append({"path": "b.py", "line": 0, "end_line": 0}),
            "non-integer measure": lambda item: item.update(measure={"value": "12", "limit": 10}),
        }
        for case, mutate in cases.items():
            with self.subTest(case=case):
                item = too_complex("a.py", 1, 12)
                mutate(item)
                with self.assertRaises(ValueError):
                    validate_finding(item)


class IntroducedFindingTests(unittest.TestCase):
    def compare(self, base: dict[str, str], head: dict[str, str], base_items: list, head_items: list) -> list:
        with tempfile.TemporaryDirectory() as before, tempfile.TemporaryDirectory() as after:
            return introduced(base_items, head_items, base_tree=tree(Path(before), base), head_tree=tree(Path(after), head))

    def test_moved_lines_still_match_their_base_finding(self) -> None:
        base = {"a.py": "def old():\n    return missing\n"}
        head = {"a.py": "import os\n\n\ndef old():\n    return missing\n"}
        self.assertEqual(self.compare(base, head, [undefined("a.py", 2)], [undefined("a.py", 5)]), [])

    def test_the_same_text_in_another_function_is_new(self) -> None:
        base = {"a.py": "def old():\n    return missing\n"}
        head = {"a.py": "def old():\n    return missing\n\ndef new():\n    return missing\n"}
        new = self.compare(base, head, [undefined("a.py", 2)], [undefined("a.py", 2), undefined("a.py", 5)])
        self.assertEqual([item["location"]["line"] for item in new], [5])

    def test_each_duplicate_needs_its_own_base_finding(self) -> None:
        base = {"a.py": "missing\n"}
        head = {"a.py": "missing\nmissing\n"}
        new = self.compare(base, head, [undefined("a.py", 1)], [undefined("a.py", 1), undefined("a.py", 2)])
        self.assertEqual(len(new), 1)

    def test_a_measured_finding_is_new_only_when_it_grows(self) -> None:
        base = {"a.py": "def choose(value):\n    pass\n"}
        head = {"a.py": "def choose(value, extra=None):\n    pass\n"}
        for value, expected in ((11, 0), (12, 0), (13, 1)):
            with self.subTest(value=value):
                new = self.compare(base, head, [too_complex("a.py", 1, 12)], [too_complex("a.py", 1, value)])
                self.assertEqual(len(new), expected)


class EvaluateTests(unittest.TestCase):
    def test_text_is_evaluated_as_the_named_file_with_shared_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory), {"pyproject.toml": "[tool.example]\n"})
            seen: list[tuple[str, ...]] = []

            def run(tree_root: Path, paths: list[str]) -> list[dict]:
                seen.append(tuple(sorted(p.relative_to(tree_root).as_posix() for p in tree_root.rglob("*.*"))))
                return lines_with("missing")(tree_root, paths)

            with text_context("cli/new.py", "value = missing\n", root=root, shared=("pyproject.toml",)) as context:
                document = evaluate(context, [Evaluator("lint", run)])
        self.assertEqual(seen, [("cli/new.py", "pyproject.toml")])
        self.assertEqual(document["context"], {"form": "text", "base": None, "head": None, "paths": ["cli/new.py"]})
        self.assertEqual([item["location"] for item in document["findings"]], [location("cli/new.py", 1)])

    def test_outcomes_record_skipped_failed_and_out_of_scope_evaluators(self) -> None:
        def broken(_root: Path, _paths: list[str]) -> list[dict]:
            raise RuntimeError("tool crashed")

        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory), {"cli/a.py": "missing\n"})
            document = evaluate(Context("files", root, ("cli/a.py",)), [
                Evaluator("lint", lines_with("missing")),
                Evaluator("types", lines_with("missing"), forms=frozenset({"change"})),
                Evaluator("deploy", lines_with("missing"), roots=("deploy",)),
                Evaluator("inspection", broken, required=False),
            ])
        self.assertEqual(
            [(item["evaluator"], item["status"]) for item in document["evaluations"]],
            [("lint", "completed"), ("types", "skipped"), ("deploy", "skipped"), ("inspection", "failed")],
        )
        self.assertEqual(document["evaluations"][3]["error"], "RuntimeError: tool crashed")
        self.assertEqual(len(document["findings"]), 1)

    def test_an_invalid_finding_fails_its_evaluator(self) -> None:
        def malformed(_root: Path, _paths: list[str]) -> list[dict]:
            return [{"tool": "lint"}]

        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory), {"a.py": "\n"})
            document = evaluate(Context("files", root, ("a.py",)), [Evaluator("lint", malformed)])
        self.assertEqual(document["evaluations"][0]["status"], "failed")
        self.assertEqual(document["findings"], [])

    def test_project_reach_reads_its_roots_but_reports_only_the_requested_files(self) -> None:
        seen: list[list[str]] = []

        def project(root: Path, paths: list[str]) -> list[dict]:
            seen.append(paths)
            return lines_with("missing", "types")(root, paths)

        with tempfile.TemporaryDirectory() as directory:
            root = tree(Path(directory), {"cli/a.py": "missing\n", "cli/b.py": "missing\n", "docs/c.md": "missing\n"})
            types = Evaluator("types", project, roots=("cli",), reach="project")
            document = evaluate(Context("files", root, ("cli/a.py",)), [types])
            outside = evaluate(Context("files", root, ("docs/c.md",)), [types])
        self.assertEqual(seen, [["cli"]])
        self.assertEqual([item["location"]["path"] for item in document["findings"]], ["cli/a.py"])
        self.assertEqual(outside["evaluations"][0]["status"], "skipped")

    def test_excluded_paths_are_neither_judged_nor_reported(self) -> None:
        with tempfile.TemporaryDirectory() as before, tempfile.TemporaryDirectory() as after:
            files = {"cli/a.py": "missing\n", "cli/vendor/b.py": "missing\n"}
            base = tree(Path(before), {"cli/a.py": "ok\n", "cli/vendor/b.py": "ok\n"})
            head = tree(Path(after), files)
            project = Evaluator("types", lines_with("missing", "types"), roots=("cli",), excluded=("cli/vendor",),
                                reach="project")
            local = Evaluator("lint", lines_with("missing"), excluded=("cli/vendor",))
            requested = evaluate(Context("files", head, tuple(files)), [project, local])
            changed = evaluate(Context("change", head, tuple(files), base=base), [project, local])
            vendored = evaluate(Context("files", head, ("cli/vendor/b.py",)), [project, local])
        for document in (requested, changed):
            self.assertEqual(
                [(item["tool"], item["location"]["path"]) for item in document["findings"]],
                [("types", "cli/a.py"), ("lint", "cli/a.py")],
            )
        self.assertEqual([item["status"] for item in vendored["evaluations"]], ["skipped", "skipped"])

    def test_a_change_reports_only_new_findings_and_project_reach_sees_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as before, tempfile.TemporaryDirectory() as after:
            base = tree(Path(before), {"cli/old.py": "missing\n", "cli/caller.py": "ok\n"})
            head = tree(Path(after), {
                "cli/old.py": "missing\nmissing here\n",
                "cli/caller.py": "missing now\n",  # Unchanged by the change, but broken by it.
            })
            context = Context("change", head, ("cli/old.py",), base=base)
            document = evaluate(context, [
                Evaluator("lint", lines_with("missing")),
                Evaluator("types", lines_with("missing", "types"), roots=("cli",), reach="project"),
            ])
        found = [(item["tool"], item["location"]["path"], item["location"]["line"]) for item in document["findings"]]
        self.assertEqual(found, [("lint", "cli/old.py", 2), ("types", "cli/caller.py", 1), ("types", "cli/old.py", 2)])


if __name__ == "__main__":
    unittest.main()
