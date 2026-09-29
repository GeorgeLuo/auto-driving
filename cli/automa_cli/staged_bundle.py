"""Shared staged-bundle import swaps and activation writes.

Memory and perception both replace process-global modules while a staged
controller bundle is active. They share one lock because they edit the same
``sys.modules`` table. Each caller names the top-level modules that come from
the bundle; every other module keeps the host process's classes.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


_IMPORT_LOCK = threading.RLock()


class StagedBundleImport:
    """Swap one bundle's modules into the process, then restore the host's."""

    def __init__(self, bundle_root: Path, prefixes: tuple[str, ...]) -> None:
        self.bundle_root = str(bundle_root)
        self.prefixes = prefixes
        self.modules: dict[str, Any] = {}

    @contextmanager
    def activate(self) -> Iterator[None]:
        with _IMPORT_LOCK:
            cached = {
                name: module
                for name, module in list(sys.modules.items())
                if self._is_bundle_module(name)
            }
            for name in cached:
                sys.modules.pop(name, None)
            sys.modules.update(self.modules)
            previous_dont_write_bytecode = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            sys.path.insert(0, self.bundle_root)
            try:
                yield
            finally:
                self.modules = {
                    name: module
                    for name, module in list(sys.modules.items())
                    if self._is_bundle_module(name)
                }
                for name in list(sys.modules):
                    if self._is_bundle_module(name):
                        sys.modules.pop(name, None)
                sys.modules.update(cached)
                try:
                    sys.path.remove(self.bundle_root)
                except ValueError:
                    pass
                sys.dont_write_bytecode = previous_dont_write_bytecode

    def _is_bundle_module(self, name: str) -> bool:
        return any(
            name == prefix or name.startswith(f"{prefix}.")
            for prefix in self.prefixes
        )


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Replace a JSON file in one step so a reader never sees a partial write.

    An existing file keeps its permission bits. The parent directory must
    already exist.
    """

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            mode = None
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            stream.write(json.dumps(payload, indent=2, sort_keys=True))
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
