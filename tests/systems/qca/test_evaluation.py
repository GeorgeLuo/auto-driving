from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from qca.evaluation import Evaluator, change_context, evaluate, files_context
from qca.evaluators import structure

BRANCHES = "def choose(value):\n{body}    return -1\n"


def branches(count: int) -> str:
    return BRANCHES.format(body="".join(f"    if value == {i}:\n        return {i}\n" for i in range(count)))


class Repository:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Quality Test")
        self.git("config", "user.email", "quality@example.test")

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def write(self, files: dict[str, str | None]) -> None:
        for name, text in files.items():
            path = self.root / name
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def commit(self, files: dict[str, str | None], message: str) -> str:
        self.write(files)
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")


class ChangeContextTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Repository(Path(directory.name))
        self.base = self.repo.commit({
            "cli/choose.py": branches(11),
            "cli/gate.py": "def ready(a, b, c):\n    return a and b and c\n",
            "config.toml": "rules = 'base'\n",
            "baseline.json": "{}\n",
        }, "base")

    def test_a_pull_request_is_compared_with_its_merge_base_using_head_configuration(self) -> None:
        self.repo.git("switch", "-qc", "feature")
        head = self.repo.commit({"cli/new.py": "x = 1\n", "config.toml": "rules = 'head'\n", "baseline.json": None},
                                "feature")
        self.repo.git("switch", "-q", "main")
        self.repo.commit({"cli/later.py": "y = 2\n"}, "main moves on")
        with change_context(self.repo.root, "main", head, shared=("config.toml", "baseline.json")) as context:
            self.assertEqual(context.describe()["base"], self.base)
            self.assertEqual(context.paths, ("baseline.json", "cli/new.py", "config.toml"))
            assert context.base is not None
            self.assertEqual((context.base / "config.toml").read_text(), "rules = 'head'\n")
            self.assertFalse((context.base / "baseline.json").exists())
            self.assertFalse((context.head / "cli" / "later.py").exists())

    def test_the_working_tree_change_includes_untracked_files(self) -> None:
        self.repo.write({"cli/gate.py": "def ready(a, b):\n    return a and b\n", "cli/draft.py": "z = 3\n"})
        with change_context(self.repo.root, "main") as context:
            self.assertEqual(context.head, self.repo.root)
            self.assertIsNone(context.describe()["head"])
            self.assertEqual(context.paths, ("cli/draft.py", "cli/gate.py"))

    def test_qca_reports_new_conditionals_and_growth_on_an_unchanged_signature(self) -> None:
        head = self.repo.commit({
            "cli/choose.py": branches(12),
            "cli/gate.py": "def ready(a, b, c):\n    return a and b and c\n\n\ndef armed(a, b, c):\n    return a or b and c\n",
        }, "grow")
        with change_context(self.repo.root, self.base, head) as context:
            document = evaluate(context, [Evaluator("qca", structure)])
        found = [(item["rule"], item["location"].get("symbol"), item["measure"]) for item in document["findings"]]
        self.assertEqual(found, [
            ("structure.opaque_conditionals.opaque_conditional", "armed", None),
            ("structure.complexity.cyclomatic_complexity", "choose", {"value": 13, "limit": 10}),
        ])

    def test_files_expand_directories_to_tracked_and_unignored_files(self) -> None:
        self.repo.write({".gitignore": "cache/\n", "cli/cache/skip.py": "x = 1\n", "cli/draft.py": "z = 3\n"})
        with files_context(self.repo.root, ["cli"]) as context:
            self.assertEqual(context.paths, ("cli/choose.py", "cli/draft.py", "cli/gate.py"))


if __name__ == "__main__":
    unittest.main()
