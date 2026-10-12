from __future__ import annotations

import unittest

from tests.support.cli_runner import run_automa


class MemoryInspectTests(unittest.TestCase):
    def test_help_lists_inspect_and_not_replay(self) -> None:
        listing = run_automa("vehicles", "memory", "help").stdout
        self.assertIn("inspect", listing)
        for removed in ("replay", "enable", "disable"):
            self.assertNotIn(removed, listing)
        flags = run_automa("vehicles", "memory", "inspect", "--help").stdout
        for flag in ("--preset", "--plugin", "--record", "--json"):
            self.assertIn(flag, flags)
        for live in ("--id", "--frames", "--interval-s", "--timeout-s"):
            self.assertNotIn(live, flags)


if __name__ == "__main__":
    unittest.main(verbosity=2)
