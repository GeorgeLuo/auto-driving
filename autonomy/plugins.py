"""Step-independent plugin discovery and selection.

A plugin declares its own ID as its ``plugin_id`` attribute; that is the only
ID it has. IDs are unique within a step. Whoever owns a catalog resolves
conflicts: registering a second definition under an ID the step already has
raises ``DuplicatePluginIdError``, and a loaded plugin must declare the ID it
was selected under (``require_plugin_id``).

A runner is constructed from one ordered sequence of definitions. It loads and
validates that sequence once and keeps the instances. A later selection is a new
runner, not a change to this one. ``plugin_report`` describes the applied
instances. Definitions come from packaged entries or ``LocalPluginCatalog``.
Upload stores a source
revision without selecting or importing it; existing selections retain their
definitions until explicitly changed. An uploaded source is named by its
sha256, ``<sha256>.py:Name``, so the catalog that received it and every
recording that ran it hold the same code under the same name.
``instantiate_plugin`` constructs a definition's entrypoint with its config; a
step checks the resulting instance against its own plugin protocol.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
import threading
import weakref
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar


class PluginManagementError(ValueError):
    """A definition or selection could not be resolved."""


class DuplicatePluginIdError(PluginManagementError):
    """Two plugin definitions in one step share an ID."""


@dataclass(frozen=True)
class PluginDefinition:
    """The common fields needed to select a plugin for any step."""

    step: str
    plugin_id: str
    entrypoint: str
    config: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def executable_entrypoint(self) -> str:
        """An uploaded source is independent of the installed module namespace."""

        source = self.metadata.get("source_file")
        if source is not None:
            return f"{source}:{self.entrypoint.partition(':')[2]}"
        return self.entrypoint

    def __post_init__(self) -> None:
        for name in ("step", "plugin_id", "entrypoint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PluginManagementError(f"plugin {name} must be a non-empty string")
        for name in ("config", "metadata"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise PluginManagementError(f"plugin {name} must be an object")
            object.__setattr__(self, name, deepcopy(dict(value)))

    @classmethod
    def declared(
        cls,
        step: str,
        entrypoint: str,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> PluginDefinition:
        """A definition under the ID the entrypoint declares; nothing is constructed."""

        return cls(
            step=step,
            plugin_id=declared_plugin_id(entrypoint),
            entrypoint=entrypoint,
            config=config or {},
            metadata=metadata or {},
        )


_UPLOADED_SOURCE_ROOTS: dict[str, None] = {}
_UPLOADED_SOURCE_LOCK = threading.Lock()


def add_uploaded_source_root(path: Path | str) -> None:
    """Let uploaded sources import from ``path``: a catalog's store or a recording's."""

    with _UPLOADED_SOURCE_LOCK:
        _UPLOADED_SOURCE_ROOTS[str(path)] = None


def uploaded_source(name: str) -> Path:
    """The file holding uploaded source ``<sha256>.py``; every copy is the same code."""

    with _UPLOADED_SOURCE_LOCK:
        roots = list(_UPLOADED_SOURCE_ROOTS)
    for root in reversed(roots):
        path = Path(root) / name
        if path.is_file():
            return path
    raise PluginManagementError(
        f"uploaded plugin source {name} is not available here; the catalog that "
        "received it and each recording that ran it keep it under that name"
    )


class LocalPluginCatalog:
    """Definitions by step and ID; an ID appears at most once per step."""

    def __init__(self, definitions: Iterable[PluginDefinition] = ()) -> None:
        self._definitions: dict[tuple[str, str], PluginDefinition] = {}
        self._lock = threading.RLock()
        self.version = 0
        self._sources: str | None = None
        self._collections: dict[str, list[str]] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: PluginDefinition) -> None:
        with self._lock:
            key = (definition.step, definition.plugin_id)
            existing = self._definitions.get(key)
            if existing is not None:
                raise DuplicatePluginIdError(
                    f"duplicate {definition.step} plugin id {definition.plugin_id!r}: "
                    f"declared by {existing.entrypoint} and {definition.entrypoint}"
                )
            self._definitions[key] = definition
            self.version += 1

    def include(self, definitions: Iterable[PluginDefinition]) -> None:
        """Seed missing entries without replacing an available uploaded revision."""

        with self._lock:
            for definition in definitions:
                if (definition.step, definition.plugin_id) not in self._definitions:
                    self.register(definition)

    def upload(
        self, *, step: str, plugin_id: str, entrypoint: str, source: bytes,
        filename: str = "plugin.py", config: Mapping[str, Any] | None = None,
    ) -> PluginDefinition:
        """Store source and publish its definition; loading belongs to selection.

        Source that does not compile is refused here, naming its line.
        Sources live outside installed packages for this catalog's lifetime.
        Each source is kept once under its sha256, so an existing selection
        retains its source when this ID gains another available revision.
        """

        definition = PluginDefinition(step, plugin_id, entrypoint, config or {})
        try:
            compile(source, filename, "exec")
        except SyntaxError as exc:
            raise ValueError(f"{filename}:{exc.lineno}: {exc.msg}") from exc
        revision = hashlib.sha256(source).hexdigest()
        with self._lock:
            if self._sources is None:
                self._sources = tempfile.mkdtemp(prefix="automa-plugins-")
                weakref.finalize(self, shutil.rmtree, self._sources, ignore_errors=True)
                add_uploaded_source_root(self._sources)
            path = Path(self._sources) / f"{revision}.py"
            if not path.is_file():
                descriptor, partial = tempfile.mkstemp(suffix=".partial", dir=self._sources)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(source)
                os.replace(partial, path)
            definition = PluginDefinition(
                step, plugin_id, entrypoint, definition.config,
                {"source_file": path.name, "revision": revision, "filename": filename},
            )
            self._definitions[(step, plugin_id)] = definition
            self.version += 1
            return definition

    def upload_collection(self, name: str, plugins: list[dict[str, Any]],
                          sources: Mapping[str, str]) -> tuple[PluginDefinition, ...]:
        """Register the plugins using ordinary uploads, then publish their membership."""
        from autonomy.decision_cycle.activation import require_step
        from autonomy.plugin_collections import collection_source

        if not isinstance(name, str) or not name.strip():
            raise ValueError("collection name must be a non-empty string")
        if not isinstance(plugins, list) or not plugins:
            raise ValueError("collection plugins must be a non-empty list")
        references = []
        for item in plugins:
            if not isinstance(item["name"], str):
                raise ValueError("collection plugin name must be step/plugin")
            step, separator, plugin_id = item["name"].partition("/")
            require_step(step)
            if not separator or not plugin_id or "/" in plugin_id:
                raise ValueError("collection plugin name must be step/plugin")
            PluginDefinition(step, plugin_id, item["entrypoint"], item.get("config", {}))
            references.append(item["name"])
        if len(references) != len(set(references)):
            raise ValueError("collection plugin names must be unique")
        source = collection_source(sources, [item["entrypoint"] for item in plugins])
        with self._lock:
            definitions = tuple(self.upload(
                step=item["name"].partition("/")[0], plugin_id=item["name"].partition("/")[2],
                entrypoint=f"collection:plugin_{index}", source=source,
                filename=f"{name}.py", config=item.get("config", {}),
            ) for index, item in enumerate(plugins))
            self._collections[name] = references
            return definitions

    def collections(self) -> dict[str, list[str]]:
        with self._lock:
            return deepcopy(self._collections)

    def collection_selections(self, name: str) -> dict[str, list[str]]:
        with self._lock:
            if name not in self._collections:
                raise ValueError(f"unknown plugin collection {name!r}")
            selections: dict[str, list[str]] = {}
            for reference in self._collections[name]:
                step, _, plugin_id = reference.partition("/")
                selections.setdefault(step, []).append(plugin_id)
            return selections

    def list(self, step: str) -> tuple[PluginDefinition, ...]:
        with self._lock:
            return tuple(
                definition for (item_step, _), definition in sorted(self._definitions.items())
                if item_step == step
            )

    def read(self) -> tuple[int, tuple[PluginDefinition, ...]]:
        """Read a catalog version and its definitions together."""

        with self._lock:
            return self.version, tuple(value for _, value in sorted(self._definitions.items()))

    def resolve(self, step: str, plugin_id: str) -> PluginDefinition:
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise PluginManagementError("plugin id must be a non-empty string")
        with self._lock:
            definition = self._definitions.get((step, plugin_id))
        if definition is None:
            raise PluginManagementError(f"unknown plugin id {plugin_id!r} for step {step!r}")
        return definition


_T = TypeVar("_T")


def _import_entrypoint(entrypoint: str, *, reload_module: bool = False) -> Any:
    module_name, separator, attribute = entrypoint.partition(":")
    if not separator or not module_name or not attribute:
        raise PluginManagementError(
            f"plugin entrypoint must be 'module.path:Name', got {entrypoint!r}"
        )
    importlib.invalidate_caches()
    if module_name.endswith(".py"):
        # Uploaded files are self-contained for now. Imports between uploaded
        # modules/packages are a future loader concern, not an upload gate.
        name = "_automa_uploaded_" + hashlib.sha256(module_name.encode()).hexdigest()
        if name not in sys.modules:
            path = module_name if Path(module_name).is_absolute() else uploaded_source(module_name)
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(name, None)
                raise
        return getattr(sys.modules[name], attribute)
    module = importlib.import_module(module_name)
    if reload_module:
        module = importlib.reload(module)
    return getattr(module, attribute)


def declared_plugin_id(entrypoint: str) -> str:
    """The ``plugin_id`` the entrypoint declares, read without constructing it."""

    plugin_id = getattr(_import_entrypoint(entrypoint), "plugin_id", None)
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        raise PluginManagementError(f"plugin {entrypoint} does not declare a plugin_id")
    return plugin_id


def instantiate_plugin(definition: PluginDefinition, *, reload_module: bool = False) -> Any:
    """Import ``definition.entrypoint`` and call it with a copy of its config."""

    factory = _import_entrypoint(definition.executable_entrypoint, reload_module=reload_module)
    return factory(**deepcopy(dict(definition.config)))


def require_plugin_id(plugin: Any, definition: PluginDefinition) -> str:
    """Return the instance's ``plugin_id``; it must equal the ID it was selected under."""

    plugin_id = getattr(plugin, "plugin_id", None)
    if plugin_id != definition.plugin_id:
        raise TypeError(
            f"{definition.step} plugin {definition.entrypoint} declares plugin_id "
            f"{plugin_id!r}, selected as {definition.plugin_id!r}"
        )
    return plugin_id


def plugin_report(
    applied: Iterable[tuple[PluginDefinition, Any]],
    records: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe the applied plugins.

    ``records`` describe applied instances, matched by ``plugin_id``. Only
    identity, timing, and error are copied. A record for a plugin that is not
    applied is ignored, and duration and error stay null when omitted.
    """

    applied_pairs = _applied_pairs(applied)
    supplied = _report_records(records)
    plugins = [
        _report_plugin(definition.plugin_id, supplied.get(definition.plugin_id, {}))
        for definition, _instance in applied_pairs
    ]
    return {
        "applied_plugin_ids": [definition.plugin_id for definition, _instance in applied_pairs],
        "plugins": plugins,
    }


def _ordered_collection(value: Iterable[Any], *, what: str) -> list[Any]:
    if isinstance(value, (str, bytes, Mapping, set, frozenset)):
        raise PluginManagementError(f"{what} must be an ordered collection")
    try:
        return list(value)
    except TypeError as exc:
        raise PluginManagementError(f"{what} must be iterable") from exc


def _applied_pairs(
    applied: Iterable[tuple[PluginDefinition, _T]],
) -> tuple[tuple[PluginDefinition, _T], ...]:
    pairs: list[tuple[PluginDefinition, _T]] = []
    seen: set[str] = set()
    for item in _ordered_collection(applied, what="applied plugins"):
        definition = item[0] if isinstance(item, tuple) and len(item) == 2 else None
        if not isinstance(definition, PluginDefinition):
            raise PluginManagementError(
                "applied plugins must be (PluginDefinition, instance) pairs"
            )
        if definition.plugin_id in seen:
            raise PluginManagementError(
                f"applied plugins contain duplicate id {definition.plugin_id!r}"
            )
        seen.add(definition.plugin_id)
        pairs.append((definition, item[1]))
    return tuple(pairs)


def _report_records(
    records: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    if records is None:
        return {}
    indexed: dict[str, Mapping[str, Any]] = {}
    for record in _ordered_collection(records, what="plugin report records"):
        if not isinstance(record, Mapping):
            raise PluginManagementError("plugin report records must be objects")
        plugin_id = record.get("plugin_id")
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise PluginManagementError("plugin report records need a plugin_id")
        indexed.setdefault(plugin_id, record)
    return indexed


def _report_plugin(plugin_id: str, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "plugin_id": plugin_id,
        "duration_ms": _report_duration(record.get("duration_ms")),
        "error": _report_error(record.get("error")),
    }


def _report_duration(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PluginManagementError("plugin duration_ms must be a number")
    return value


def _report_error(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PluginManagementError("plugin error must be a string")
    return value
