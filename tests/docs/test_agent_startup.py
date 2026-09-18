from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STARTUP_FILES = (
    ROOT / "AGENTS.md",
    ROOT / "docs" / "guidance" / "agent-surface.md",
    ROOT / "docs" / "guidance" / "roles" / "engineer.md",
    ROOT / "docs" / "guidance" / "roles" / "reviewer.md",
    ROOT / ".github" / "pull_request_template.md",
)
BANNED = re.compile(
    r"\b(milestone|frontier|closeout|work-order|review unit)\b",
    re.IGNORECASE,
)


class AgentStartupTests(unittest.TestCase):
    def test_startup_files_exist(self) -> None:
        for path in STARTUP_FILES:
            with self.subTest(path=path):
                self.assertTrue(path.is_file(), path)

    def test_startup_files_do_not_route_into_parked_delivery_process(self) -> None:
        for path in STARTUP_FILES:
            text = path.read_text(encoding="utf-8")
            match = BANNED.search(text)
            with self.subTest(path=path):
                self.assertIsNone(
                    match,
                    f"{path.relative_to(ROOT)} still names "
                    f"{match.group(0)!r}" if match else "",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
