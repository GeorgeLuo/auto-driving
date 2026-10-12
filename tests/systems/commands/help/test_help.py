from __future__ import annotations

import unittest

from tests.support.cli_runner import run_automa


class HelpCommandTests(unittest.TestCase):
    def test_public_help_exits_successfully(self) -> None:
        result = run_automa("help")
        self.assertTrue(result.stdout.strip())
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
