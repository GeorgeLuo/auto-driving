"""Packaged plugin listing and selection for the replay workbench.

The workbench offers every packaged plugin of the steps whose selection the
operator can change: perception and memory. A selection is checked and built
through ``selection_activation``, the activation the CLI uses for that step,
so a preset given on the command line keeps its plugin configs. Listing a
catalog does not construct plugins;
construction happens only after the operator selects plugins for a replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.plugins import PluginDefinition
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PLUGINS,
    packaged_activation,
    selection_activation,
    step_plugins,
)


PLUGIN_CATALOG_SCHEMA = "workbench_plugin_catalog_v1"
SELECTABLE_STEPS = ("perception", "memory")


class PluginCatalogError(ValueError):
    """A bounded catalog or selection boundary failure."""

    boundary = "plugin_catalog"


@dataclass(frozen=True)
class PluginDescriptor:
    """Presentation metadata for one packaged plugin."""

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
    """One step's packaged catalog, in display order, and its digest."""

    step: str
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

        An empty selection disables plugins for this step. Replay still
        displays frames, and the other step keeps its selection.
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
            return selection_activation(self.step, plugins=list(active_ids))
        except ValueError as exc:
            raise PluginCatalogError(str(exc)) from exc

    def build(self, activation: StepActivation) -> PerceptionRunner | MemoryRunner:
        """Construct exactly the activation's plugins and configs, as the step's runner."""

        runner = PerceptionRunner if self.step == "perception" else MemoryRunner
        return runner.from_activation(activation)


def packaged_plugin_catalog(step: str) -> PluginCatalog:
    """Every packaged plugin of ``step``; the step's default selection is preselected."""

    if step not in SELECTABLE_STEPS:
        raise PluginCatalogError(
            f"plugins can be selected for {' and '.join(SELECTABLE_STEPS)}, not {step!r}"
        )
    default_ids = DEFAULT_STEP_PLUGINS[step]
    entries = step_plugins(step)
    definitions: dict[str, PluginDefinition] = {
        item.plugin_id: item
        for item in packaged_activation(step, []).plugin_manager().available
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
    return PluginCatalog(step=step, plugins=descriptors, digest=digest)


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
    "SELECTABLE_STEPS",
    "packaged_plugin_catalog",
]
