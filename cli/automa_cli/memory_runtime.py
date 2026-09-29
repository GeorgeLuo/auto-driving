"""CLI-owned memory activation persistence, bundle loading, and live sync."""

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

from autonomy.memory import ActivatedMemoryStep, MemoryActivation, read_memory_activation
from autonomy.memory.activation import memory_selection_config

_STAGED_BUNDLE_IMPORT_LOCK = threading.RLock()


def load_memory_step_from_bundle(activation: MemoryActivation) -> ActivatedMemoryStep:
    bundle = activation.payload.get("controller_bundle", {})
    root = bundle.get("root_dir") if isinstance(bundle, dict) else None
    if not root:
        return ActivatedMemoryStep(activation)
    if not Path(root).is_dir():
        raise FileNotFoundError(f"Controller bundle is missing: {root}")
    import_context = _StagedBundleImportContext(Path(root))
    # Implementations come from the staged bundle; autonomy values must retain
    # the host's class identity for DecisionCycle's MemorySnapshot checks.
    with import_context.activate():
        step = ActivatedMemoryStep(activation)
    for method_name in ("update", "reset", "snapshot"):
        method = getattr(step, method_name)

        def invoke_in_bundle(*args, _method=method, **kwargs):
            with import_context.activate():
                return _method(*args, **kwargs)

        setattr(step, method_name, invoke_in_bundle)
    return step


def _sync_live_memory_plugin_selection(
    step: ActivatedMemoryStep,
    activation_path: Path,
    *,
    loaded_config: dict[str, Any],
) -> None:
    """Synchronize CLI selection edits into the running manager before a cycle."""

    try:
        live_config = memory_selection_config(read_memory_activation(activation_path))
        # As in perception, changed code/config/catalog requires a worker restart.
        if any(live_config[key] != loaded_config[key] for key in ("plugin_specs", "plugin_configs")):
            return
        plugin_ids = live_config["plugins"]
        if tuple(plugin_ids) != step.plugin_manager.selected_ids:
            step.plugin_manager.select(plugin_ids)
    except (OSError, ValueError, TypeError):
        # An incomplete or stale activation must not replace the current set.
        return


class _StagedBundleImportContext:
    """Temporarily activate one bundle's modules for a manager-backed memory step."""

    _PREFIXES = ("implementations", "lab")

    def __init__(self, bundle_root: Path) -> None:
        self.bundle_root = str(bundle_root)
        self.modules: dict[str, Any] = {}

    @contextmanager
    def activate(self) -> Iterator[None]:
        with _STAGED_BUNDLE_IMPORT_LOCK:
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

    @classmethod
    def _is_bundle_module(cls, name: str) -> bool:
        return any(
            name == prefix or name.startswith(f"{prefix}.")
            for prefix in cls._PREFIXES
        )


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Replace a JSON activation in one step so the running worker can poll it."""

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
