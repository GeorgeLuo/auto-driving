from __future__ import annotations

import unittest

from tests.support.cli_runner import run_automa


class SelectionRebuildTests(unittest.TestCase):
    def test_cli_help_describes_selection_replay_for_every_step(self) -> None:
        help_text = " ".join(
            run_automa("vehicles", "workbench", "replay", "--help").stdout.split()
        )
        self.assertIn("Changing a step's plugins", help_text)
        self.assertIn("rebuilds every step's pipeline", help_text)
        self.assertIn("displayed frame before returning", help_text)
        self.assertIn("append newly checked plugins", help_text)
        self.assertIn("changed ordered selection uses default configs for that step", help_text)
        self.assertIn("other steps keep their selections and configs", help_text)
        self.assertIn("same ordered selection keeps the current configs and pass", help_text)


if __name__ == "__main__":
    unittest.main()
