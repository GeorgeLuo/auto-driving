"""Step-independent plugin discovery and selection.

The core tracks which plugin definitions are selected. A step owns construction,
execution, reset, and validation of the behavior declared by those definitions.
``replace_selection`` sequences that handoff when a resolved selection changes.
Definitions may come from packaged entries, explicit JSON files, or a future
catalog implementing ``PluginResolver``.
"""

from __future__ import annotations

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

    The manager is the source of truth for the desired selection. This runtime
    retains the instances already applied and reconciles them with the manager
    whenever ``apply`` is called. It does not impose a plugin-count limit or
    define how a step loads, validates, executes, or resets its plugin type.
    """

    def __init__(self, manager: PluginManager) -> None:
        if not isinstance(manager, PluginManager):
            raise PluginManagementError("manager must be a PluginManager")
        self.manager = manager
        self._applied: tuple[tuple[PluginDefinition, _T], ...] = ()

    @property
    def applied(self) -> tuple[tuple[PluginDefinition, _T], ...]:
        """The definitions and instances currently published by the step."""

        return self._applied

    def apply(
        self,
        *,
        load: Callable[[PluginDefinition], _T],
        validate: Callable[[tuple[_T, ...]], None] | None = None,
        reset: Callable[[_T], None] | None = None,
    ) -> tuple[tuple[PluginDefinition, _T], ...]:
        """Apply the manager's current selection and return its instances.

        The selection is snapshotted once. If the manager changes during
        application, the new selection is picked up by the next call.
        """

        selected = tuple(self.manager.selected)
        instances = replace_selection(
            self._applied,
            selected,
            load=load,
            validate=validate,
            reset=reset,
        )
        self._applied = tuple(zip(selected, instances, strict=True))
        return self._applied


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

    applied_pairs = _applied_pairs(applied)
    selected_definitions = _selected_definitions(selected)
    if tuple(definition for definition, _instance in applied_pairs) == selected_definitions:
        return tuple(instance for _definition, instance in applied_pairs)

    applied_by_id = {
        definition.plugin_id: (definition, instance)
        for definition, instance in applied_pairs
    }
    candidates: list[_T] = []
    for definition in selected_definitions:
        existing = applied_by_id.get(definition.plugin_id)
        if existing is not None and existing[0] == definition:
            candidates.append(existing[1])
        else:
            candidates.append(load(definition))

    published = tuple(candidates)
    if validate is not None:
        validate(published)
    if reset is not None:
        kept = {id(instance) for instance in published}
        released: set[int] = set()
        for _definition, instance in applied_pairs:
            identity = id(instance)
            if identity in kept or identity in released:
                continue
            released.add(identity)
            reset(instance)
    return published


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
