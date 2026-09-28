"""Step-independent plugin discovery and selection.

The core tracks which plugin definitions are selected. A step owns construction,
execution, reset, and validation of the behavior declared by those definitions.
Definitions may come from packaged entries, explicit JSON files, or a future
catalog implementing ``PluginResolver``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


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
