"""Packaged plugins are listed by the ID each one declares."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from unittest import mock

from autonomy.plugins import DuplicatePluginIdError
from cli.automa_cli import app
from implementations.decision_cycle import catalog


class DeclaredIdTests(unittest.TestCase):
    def tearDown(self) -> None:
        catalog._declared_entries.cache_clear()

    def _clashing_actions(self) -> contextlib.AbstractContextManager:
        # A second entry whose plugin declares an ID already taken in the step.
        entries = catalog.STEP_PLUGINS["action"]
        catalog._declared_entries.cache_clear()
        return mock.patch.dict(catalog.STEP_PLUGINS, {"action": (*entries, dict(entries[0]))})

    def test_every_step_lists_plugins_by_declared_id(self) -> None:
        for step in catalog.STEPS:
            with self.subTest(step=step):
                plugins = catalog.step_plugins(step)
                self.assertEqual(len(plugins), len(catalog.STEP_PLUGINS[step]))
                self.assertTrue(set(catalog.DEFAULT_STEP_PLUGINS[step]) <= set(plugins))

    def test_two_plugins_declaring_one_id_stop_the_step(self) -> None:
        with self._clashing_actions():
            with self.assertRaisesRegex(DuplicatePluginIdError, "duplicate action plugin id 'hold'"):
                catalog.step_plugins("action")
            self.assertEqual(sorted(catalog.step_plugins("memory")), ["bounded_evidence", "multi_obstruction_tracks"])

    def test_cli_reports_the_clash_and_exits_2(self) -> None:
        stderr = io.StringIO()
        with (
            self._clashing_actions(),
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"AUTOMA_RUNTIME_ROOT": tmp}),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            code = app.main(
                ["vehicles", "update", "action", "--id", "chase-sim-chaser", "--plugin", "hold", "--dry-run"]
            )
        self.assertEqual(code, 2)
        self.assertIn("error: duplicate action plugin id 'hold'", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
