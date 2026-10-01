"""Step-independent plugin discovery and selection.

The core tracks which plugin definitions are selected. A step owns construction,
execution, reset, and validation of the behavior declared by those definitions.
``replace_selection`` sequences that handoff when a resolved selection changes.
``PluginSelectionRuntime`` can prepare the same handoff and commit it later, so
the next selection is loaded and validated before published instances are reset.
``plugin_report`` describes the catalog, the manager's requested selection, and
the instances a step has published. Definitions may come from packaged entries,
explicit JSON files, or a future catalog implementing ``PluginResolver``.
``instantiate_plugin`` constructs a definition's entrypoint with its config; a
step checks the resulting instance against its own plugin protocol.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar


PluginReference = str | Path


class PluginManagementError(ValueError):
    """A definition or selection could not be resolved."""


@dataclass(frozen=True)
class PluginDefinition:
    """The common fields needed to select a plugin for any step."""

    step: str
    plugin_id: str
    entrypoint: str
    config: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    source: Path | None = None

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
    def from_file(cls, path: str | Path) -> PluginDefinition:
        """Read a local definition without importing or constructing its plugin."""

        source = Path(path).expanduser().resolve()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PluginManagementError(f"could not read plugin file {source}: {exc}") from exc
        if not isinstance(payload, dict):
            raise PluginManagementError(f"plugin file {source} must contain an object")
        try:
            return cls(
                step=payload["step"],
                plugin_id=payload["id"],
                entrypoint=payload["entrypoint"],
                config=payload.get("config", {}),
                metadata=payload.get("metadata", {}),
                source=source,
            )
        except (KeyError, PluginManagementError) as exc:
            raise PluginManagementError(f"invalid plugin file {source}: {exc}") from exc


class PluginResolver(Protocol):
    """An ID or file resolver; a remote catalog can provide this later."""

    def list(self, step: str) -> tuple[PluginDefinition, ...]:
        """List selectable catalog definitions without constructing plugins.

        Explicit file references can also be resolved but are not enumerated.
        """
        ...

    def resolve(self, step: str, reference: PluginReference) -> PluginDefinition: ...


class LocalPluginCatalog:
    """Packaged definitions by ID, with explicit file references as an option."""

    def __init__(self, definitions: Iterable[PluginDefinition] = ()) -> None:
        self._definitions: dict[tuple[str, str], PluginDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: PluginDefinition) -> None:
        key = (definition.step, definition.plugin_id)
        existing = self._definitions.get(key)
        if existing is not None and existing != definition:
            raise PluginManagementError(
                f"duplicate plugin id {definition.plugin_id!r} for step {definition.step!r}"
            )
        self._definitions[key] = definition

    def list(self, step: str) -> tuple[PluginDefinition, ...]:
        return tuple(
            definition for (item_step, _), definition in sorted(self._definitions.items())
            if item_step == step
        )

    def resolve(self, step: str, reference: PluginReference) -> PluginDefinition:
        if isinstance(reference, Path):
            return PluginDefinition.from_file(reference)
        if not isinstance(reference, str) or not reference.strip():
            raise PluginManagementError("plugin reference must be an ID or file path")
        definition = self._definitions.get((step, reference))
        if definition is not None:
            return definition
        if reference.endswith(".json") or "/" in reference or "\\" in reference:
            return PluginDefinition.from_file(reference)
        raise PluginManagementError(f"unknown plugin id {reference!r} for step {step!r}")


class PluginManager:
    """Manage a step's ordered active definitions without imposing a count cap."""

    def __init__(self, step: str, resolver: PluginResolver) -> None:
        if not isinstance(step, str) or not step.strip():
            raise PluginManagementError("step must be a non-empty string")
        self.step = step
        self.resolver = resolver
        self._selected: tuple[PluginDefinition, ...] = ()

    @classmethod
    def from_specs(
        cls,
        step: str,
        specs: Mapping[str, str],
        configs: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> PluginManager:
        """Build a local manager from step-scoped entrypoint and config maps."""

        if not isinstance(specs, Mapping):
            raise PluginManagementError("plugin specs must be a mapping")
        if configs is not None and not isinstance(configs, Mapping):
            raise PluginManagementError("plugin configs must be a mapping")
        plugin_configs = configs or {}
        catalog = LocalPluginCatalog(
            PluginDefinition(
                step=step,
                plugin_id=plugin_id,
                entrypoint=entrypoint,
                config=plugin_configs.get(plugin_id, {}),
            )
            for plugin_id, entrypoint in specs.items()
        )
        return cls(step, catalog)

    @property
    def available(self) -> tuple[PluginDefinition, ...]:
        """The resolver's catalog, independent of selected or applied plugins."""

        return tuple(self.resolver.list(self.step))

    @property
    def available_ids(self) -> tuple[str, ...]:
        return tuple(definition.plugin_id for definition in self.available)

    @property
    def selected(self) -> tuple[PluginDefinition, ...]:
        return self._selected

    @property
    def selected_ids(self) -> tuple[str, ...]:
        return tuple(definition.plugin_id for definition in self._selected)

    def select(
        self, references: Iterable[PluginReference | PluginDefinition]
    ) -> tuple[PluginDefinition, ...]:
        """Resolve the full selection before changing the active definitions."""

        if isinstance(references, (str, bytes, Mapping)):
            raise PluginManagementError("plugin selection must be a collection of IDs or paths")
        try:
            values = list(references)
        except TypeError as exc:
            raise PluginManagementError("plugin selection must be iterable") from exc
        if isinstance(references, (set, frozenset)):
            values.sort(key=str)
        selected: dict[str, PluginDefinition] = {}
        for reference in values:
            definition = (
                reference if isinstance(reference, PluginDefinition)
                else self.resolver.resolve(self.step, reference)
            )
            if definition.step != self.step:
                raise PluginManagementError(
                    f"plugin {definition.plugin_id!r} belongs to {definition.step!r}, "
                    f"not {self.step!r}"
                )
            existing = selected.get(definition.plugin_id)
            if existing is not None and existing != definition:
                raise PluginManagementError(
                    f"selection contains conflicting definitions for {definition.plugin_id!r}"
                )
            selected[definition.plugin_id] = definition
        self._selected = tuple(selected.values())
        return self._selected

    def add(self, reference: PluginReference) -> tuple[PluginDefinition, ...]:
        definition = self.resolver.resolve(self.step, reference)
        if definition.plugin_id in self.selected_ids:
            # A file reference may have changed since the previous selection.
            return self.select(
                definition if item.plugin_id == definition.plugin_id else item
                for item in self._selected
            )
        return self.select((*self._selected, definition))

    def remove(self, plugin_id: str) -> tuple[PluginDefinition, ...]:
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise PluginManagementError("plugin id must be a non-empty string")
        self._selected = tuple(
            definition for definition in self._selected if definition.plugin_id != plugin_id
        )
        return self._selected


_T = TypeVar("_T")


class PluginSelectionRuntime(Generic[_T]):
    """Apply a manager's selected definitions to step-owned plugin instances.

    The manager is the source of truth for the desired selection. ``prepare``
    loads and validates that selection without resetting or publishing.
    ``commit`` then resets instances the preparation does not keep and
    publishes those same instances. ``apply`` does both. Equal definitions
    keep their instances. This runtime does not impose a plugin-count limit
    or define how a step loads, validates, executes, or resets its plugin type.
    """

    def __init__(self, manager: PluginManager) -> None:
        if not isinstance(manager, PluginManager):
            raise PluginManagementError("manager must be a PluginManager")
        self.manager = manager
        self._applied: tuple[tuple[PluginDefinition, _T], ...] = ()
        self._prepared: tuple[tuple[PluginDefinition, _T], ...] | None = None

    @property
    def applied(self) -> tuple[tuple[PluginDefinition, _T], ...]:
        """The definitions and instances currently published by the step."""

        return self._applied

    def prepare(
        self,
        *,
        load: Callable[[PluginDefinition], _T],
        validate: Callable[[tuple[_T, ...]], None] | None = None,
    ) -> tuple[tuple[PluginDefinition, _T], ...]:
        """Load and validate the manager's current selection.

        Nothing is reset or published. Load and validation failures leave the
        applied instances, and any earlier preparation, unchanged. A successful
        call replaces an outstanding preparation.
        """

        staged = _stage_selection(
            self._applied,
            self.manager.selected,
            load=load,
            validate=validate,
        )
        self._prepared = staged
        return staged

    def commit(
        self,
        *,
        reset: Callable[[_T], None] | None = None,
    ) -> tuple[tuple[PluginDefinition, _T], ...]:
        """Reset removed instances and publish the prepared selection.

        The prepared snapshot is published as staged; the manager is not read
        again. A reset failure leaves the previous publication in place and
        drops the preparation.
        """

        if self._prepared is None:
            raise PluginManagementError(
                "selection must be prepared before it is committed"
            )
        if reset is not None and not callable(reset):
            raise PluginManagementError("reset must be callable")
        prepared = self._prepared
        self._prepared = None
        _release_removed(self._applied, prepared, reset=reset)
        self._applied = prepared
        return self._applied

    def discard(self) -> None:
        """Drop a prepared selection without resetting applied or prepared instances."""

        self._prepared = None

    def apply(
        self,
        *,
        load: Callable[[PluginDefinition], _T],
        validate: Callable[[tuple[_T, ...]], None] | None = None,
        reset: Callable[[_T], None] | None = None,
    ) -> tuple[tuple[PluginDefinition, _T], ...]:
        """Apply the manager's current selection and return its instances.

        The selection is snapshotted once. If the manager changes during
        application, the new selection is picked up by the next call. This
        restages from the manager and does not commit an earlier preparation.
        """

        self.prepare(load=load, validate=validate)
        return self.commit(reset=reset)


def instantiate_plugin(definition: PluginDefinition, *, reload_module: bool = False) -> Any:
    """Import ``definition.entrypoint`` and call it with a copy of its config."""

    module_name, separator, attribute = definition.entrypoint.partition(":")
    if not separator or not module_name or not attribute:
        raise PluginManagementError(
            f"plugin entrypoint must be 'module.path:Name', got {definition.entrypoint!r}"
        )
    importlib.invalidate_caches()
    module = importlib.import_module(module_name)
    if reload_module:
        module = importlib.reload(module)
    factory = getattr(module, attribute)
    return factory(**deepcopy(dict(definition.config)))


def require_plugin_id(plugin: Any, definition: PluginDefinition) -> str:
    """Return the instance's declared ``plugin_id``; it must be a non-empty string."""

    plugin_id = getattr(plugin, "plugin_id", None)
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        raise TypeError(
            f"{definition.step} plugin {definition.entrypoint} must declare a non-empty plugin_id"
        )
    return plugin_id


def replace_selection(
    applied: Iterable[tuple[PluginDefinition, _T]],
    selected: Iterable[PluginDefinition],
    *,
    load: Callable[[PluginDefinition], _T],
    validate: Callable[[tuple[_T, ...]], None] | None = None,
    reset: Callable[[_T], None] | None = None,
) -> tuple[_T, ...]:
    """Return the instances a step can publish for an already resolved selection.

    Equal definitions keep their instances. Other definitions are passed to
    ``load``. ``validate`` then sees the full sequence. Only after it returns
    are instances that are not in that sequence passed to ``reset``. A failure
    from ``load`` or ``validate`` does not call ``reset``. This does not import
    plugins or change a ``PluginManager``.
    """

    if not callable(load):
        raise PluginManagementError("load must be callable")
    if validate is not None and not callable(validate):
        raise PluginManagementError("validate must be callable")
    if reset is not None and not callable(reset):
        raise PluginManagementError("reset must be callable")
    staged = _stage_selection(applied, selected, load=load, validate=validate)
    _release_removed(applied, staged, reset=reset)
    return tuple(instance for _definition, instance in staged)


def plugin_report(
    manager: PluginManager,
    applied: Iterable[tuple[PluginDefinition, Any]],
    records: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe availability, the requested selection, and published plugins.

    ``records`` describe applied instances, matched by catalog ``plugin_id``.
    Only identity, timing, and error are copied. A record for a plugin that is
    not applied is ignored, and duration and error stay null when omitted.
    """

    if not isinstance(manager, PluginManager):
        raise PluginManagementError("manager must be a PluginManager")
    applied_pairs = _applied_pairs(applied)
    supplied = _report_records(records)
    plugins = [
        _report_plugin(definition.plugin_id, supplied.get(definition.plugin_id, {}))
        for definition, _instance in applied_pairs
    ]
    return {
        "available_plugin_ids": sorted(manager.available_ids),
        "selected_plugin_ids": list(manager.selected_ids),
        "applied_plugin_ids": [definition.plugin_id for definition, _instance in applied_pairs],
        "plugins": plugins,
    }


def _stage_selection(
    applied: Iterable[tuple[PluginDefinition, _T]],
    selected: Iterable[PluginDefinition],
    *,
    load: Callable[[PluginDefinition], _T],
    validate: Callable[[tuple[_T, ...]], None] | None = None,
) -> tuple[tuple[PluginDefinition, _T], ...]:
    """Return the next instances paired with their definitions, without reset.

    Equal definitions keep their instances and do not call ``load`` or
    ``validate``. Otherwise ``load`` fills the gaps and ``validate`` sees the
    full sequence. A failure from either leaves the caller in control of the
    previous instances.
    """

    if not callable(load):
        raise PluginManagementError("load must be callable")
    if validate is not None and not callable(validate):
        raise PluginManagementError("validate must be callable")

    applied_pairs = _applied_pairs(applied)
    selected_definitions = _selected_definitions(selected)
    if tuple(definition for definition, _instance in applied_pairs) == selected_definitions:
        return applied_pairs

    applied_by_id = {
        definition.plugin_id: (definition, instance)
        for definition, instance in applied_pairs
    }
    staged: list[tuple[PluginDefinition, _T]] = []
    for definition in selected_definitions:
        existing = applied_by_id.get(definition.plugin_id)
        if existing is not None and existing[0] == definition:
            staged.append(existing)
        else:
            staged.append((definition, load(definition)))

    if validate is not None:
        validate(tuple(instance for _definition, instance in staged))
    return tuple(staged)


def _release_removed(
    applied: Iterable[tuple[PluginDefinition, _T]],
    staged: Iterable[tuple[PluginDefinition, _T]],
    *,
    reset: Callable[[_T], None] | None,
) -> None:
    """Reset applied instances that the staged selection does not keep."""

    if reset is None:
        return
    if not callable(reset):
        raise PluginManagementError("reset must be callable")
    kept = {id(instance) for _definition, instance in _applied_pairs(staged)}
    released: set[int] = set()
    for _definition, instance in _applied_pairs(applied):
        identity = id(instance)
        if identity in kept or identity in released:
            continue
        released.add(identity)
        reset(instance)


def _ordered_collection(value: Iterable[Any], *, what: str) -> list[Any]:
    if isinstance(value, (str, bytes, Mapping, set, frozenset)):
        raise PluginManagementError(f"{what} must be an ordered collection")
    try:
        return list(value)
    except TypeError as exc:
        raise PluginManagementError(f"{what} must be iterable") from exc


def _selected_definitions(
    selected: Iterable[PluginDefinition],
) -> tuple[PluginDefinition, ...]:
    chosen: dict[str, PluginDefinition] = {}
    ordered: list[PluginDefinition] = []
    for definition in _ordered_collection(selected, what="selected plugins"):
        if not isinstance(definition, PluginDefinition):
            raise PluginManagementError("selected plugins must be PluginDefinition values")
        existing = chosen.get(definition.plugin_id)
        if existing is None:
            chosen[definition.plugin_id] = definition
            ordered.append(definition)
            continue
        if existing != definition:
            raise PluginManagementError(
                f"selection contains conflicting definitions for {definition.plugin_id!r}"
            )
    return tuple(ordered)


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
        "implementation_id": _report_identifier(record.get("implementation_id")),
        "duration_ms": _report_duration(record.get("duration_ms")),
        "error": _report_error(record.get("error")),
    }


def _report_identifier(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PluginManagementError("plugin implementation_id must be a non-empty string")
    return value


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
