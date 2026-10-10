from __future__ import annotations

import stat
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

from cli.automa_cli.staged_bundle import StagedBundleImport, write_json_atomically


HOST_NAME = "bundle_probe_host.marker"
SWAPPED_NAME = "bundle_probe_swap.marker"


class StagedBundleTests(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop(HOST_NAME, None)
        sys.modules.pop(SWAPPED_NAME, None)

    def test_activation_swaps_only_the_declared_prefixes(self) -> None:
        host = types.ModuleType(HOST_NAME)
        swapped_host = types.ModuleType(SWAPPED_NAME)
        sys.modules[HOST_NAME] = host
        sys.modules[SWAPPED_NAME] = swapped_host
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = StagedBundleImport(root, ("bundle_probe_swap",))
            with context.activate():
                self.assertNotIn(SWAPPED_NAME, sys.modules)
                self.assertIs(sys.modules[HOST_NAME], host)
                self.assertEqual(sys.path[0], str(root))
                bundle_module = types.ModuleType(SWAPPED_NAME)
                sys.modules[SWAPPED_NAME] = bundle_module
            self.assertIs(sys.modules[SWAPPED_NAME], swapped_host)
            self.assertIs(sys.modules[HOST_NAME], host)
            self.assertNotEqual(sys.path[0], str(root))
            with context.activate():
                self.assertIs(sys.modules[SWAPPED_NAME], bundle_module)
                self.assertIs(sys.modules[HOST_NAME], host)
            self.assertIs(sys.modules[SWAPPED_NAME], swapped_host)

    def test_memory_and_perception_prefixes_share_one_import_lock(self) -> None:
        release = threading.Event()
        holding = threading.Event()
        acquired = threading.Event()

        def hold() -> None:
            with StagedBundleImport(Path("."), ("bundle_probe_hold",)).activate():
                holding.set()
                self.assertTrue(release.wait(2))

        def wait_for_lock() -> None:
            with StagedBundleImport(Path("."), ("bundle_probe_wait",)).activate():
                acquired.set()

        first = threading.Thread(target=hold)
        second = threading.Thread(target=wait_for_lock)
        first.start()
        self.assertTrue(holding.wait(2))
        second.start()
        second.join(0.2)
        self.assertFalse(acquired.is_set())
        release.set()
        second.join(2)
        first.join(2)
        self.assertTrue(acquired.is_set())

    def test_atomic_write_replaces_json_and_keeps_existing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text("old", encoding="utf-8")
            path.chmod(0o640)
            write_json_atomically(path, {"b": 1, "a": 2})
            self.assertEqual(path.read_text(encoding="utf-8"), '{\n  "a": 2,\n  "b": 1\n}')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(list(Path(tmp).glob(".active.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
