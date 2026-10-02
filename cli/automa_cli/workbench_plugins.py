"""Packaged perception plugin listing and selection for the replay workbench.

The workbench offers every packaged perception plugin. A selection is checked
and built through the same ``perception_activation`` the CLI uses. Listing the
catalog does not construct plugins; construction happens only after the
operator selects plugins for a replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.perception.interface import PerceptionBackend
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.plugins import PluginDefinition
from implementations.decision_cycle.catalog import (
    packaged_activation,
    perception_activation,
    step_plugins,
)
from implementations.decision_cycle.perception.presets import (
    DEFAULT_PERCEPTION_PRESET,
    PERCEPTION_PRESETS,
)


PLUGIN_CATALOG_SCHEMA = "workbench_plugin_catalog_v1"


class PluginCatalogError(ValueError):
    """A bounded catalog or selection boundary failure."""

    boundary = "plugin_catalog"


@dataclass(frozen=True)
class PluginDescriptor:
    """Presentation metadata for one packaged perception plugin."""

    plugin_id: str
    description: str
    entrypoint: str
    config: dict[str, Any]
    default: bool = False

    def to_dict(self, *, active_ids: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "id": self.plugin_id,
            "name": self.plugin_id,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "config": _json_safe(self.config),
            "default": self.default,
            "active": self.plugin_id in active_ids,
        }


@dataclass(frozen=True)
class PluginCatalog:
    """The packaged perception catalog, in display order, and its digest."""

    plugins: tuple[PluginDescriptor, ...]
    digest: str

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.plugin_id for item in self.plugins)

    @property
    def default_ids(self) -> tuple[str, ...]:
        return tuple(item.plugin_id for item in self.plugins if item.default)

    def to_dict(self, *, active_ids: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "schema": PLUGIN_CATALOG_SCHEMA,
            "digest": self.digest,
            "plugins": [item.to_dict(active_ids=active_ids) for item in self.plugins],
        }

    def normalize_selection(self, active_ids: Sequence[str] | None) -> tuple[str, ...]:
        """Validate ids while preserving the given order.

        An empty selection is raw-capture mode: replay still displays frames,
        but no perception plugin runs.
        """

        raw_values = [] if active_ids is None else list(active_ids)
        if any(not isinstance(value, str) for value in raw_values):
            raise PluginCatalogError("active_plugin_ids must contain non-empty strings")
        values = [value.strip() for value in raw_values]
        if any(not value for value in values):
            raise PluginCatalogError("active_plugin_ids must contain non-empty strings")
        if len(values) != len(set(values)):
            raise PluginCatalogError("active_plugin_ids must not contain duplicates")
        return self.activation(values).plugins

    def activation(self, active_ids: Sequence[str]) -> StepActivation:
        """The CLI's activation for these ids; unknown ids are a catalog error."""

        try:
            return perception_activation(plugins=list(active_ids))
        except ValueError as exc:
            raise PluginCatalogError(str(exc)) from exc

    def build_mapper(self, active_ids: Sequence[str]) -> PerceptionBackend:
        """Construct exactly the selected packaged perception plugins."""

        return PerceptionRunner.from_activation(self.activation(active_ids))


def packaged_plugin_catalog() -> PluginCatalog:
    """Every packaged perception plugin; the default preset's are preselected."""

    default_ids = tuple(PERCEPTION_PRESETS[DEFAULT_PERCEPTION_PRESET]["plugins"])
    entries = step_plugins("perception")
    definitions: dict[str, PluginDefinition] = {
        item.plugin_id: item
        for item in packaged_activation("perception", []).plugin_manager().available
    }
    # Defaults keep their execution order and lead the list.
    plugin_ids = list(dict.fromkeys([*default_ids, *sorted(definitions)]))
    descriptors = tuple(
        PluginDescriptor(
            plugin_id=plugin_id,
            description=str(entries[plugin_id].get("description") or ""),
            entrypoint=definitions[plugin_id].entrypoint,
            config=dict(definitions[plugin_id].config),
            default=plugin_id in default_ids,
        )
        for plugin_id in plugin_ids
    )
    payload = [item.to_dict() for item in descriptors]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PluginCatalog(plugins=descriptors, digest=digest)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


__all__ = [
    "PLUGIN_CATALOG_SCHEMA",
    "PluginCatalog",
    "PluginCatalogError",
    "PluginDescriptor",
    "packaged_plugin_catalog",
]
