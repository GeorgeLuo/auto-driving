"""Live catalog listing and selection for the replay workbench.

The workbench offers every packaged plugin of the steps whose selection the
operator can change: perception, memory and proposal. A selection is checked
and built by ``step_selection``, the activation the CLI stages for that step:
perception and memory select a preset or a plugin list, so a preset given on
the command line keeps its plugin configs; proposal has no presets and selects
a plugin list. Listing a catalog does not construct plugins; construction
happens only after the operator selects plugins for a replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Sequence

from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.steps import step_runner
from autonomy.plugins import LocalPluginCatalog, PluginDefinition
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PLUGINS,
    STEP_PRESETS,
    packaged_activation,
    selection_activation,
    step_plugins,
)


PLUGIN_CATALOG_SCHEMA = "workbench_plugin_catalog_v1"
SELECTABLE_STEPS = ("perception", "memory", "proposal")


def step_selection(
    step: str,
    *,
    preset: str | None = None,
    plugins: Sequence[str] | None = None,
) -> StepActivation:
    """The activation the CLI stages for this selection of ``step``.

    A step with presets takes a preset or an ordered plugin list, and its
    default preset with neither. A step without presets takes an ordered
    plugin list, and its default plugins without one.
    """

    if step in STEP_PRESETS:
        return selection_activation(step, preset=preset, plugins=plugins)
    if preset is not None:
        raise ValueError(f"{step} has no presets; select its plugins instead")
    return packaged_activation(step, plugins)


class PluginCatalogError(ValueError):
    """A bounded catalog or selection boundary failure."""

    boundary = "plugin_catalog"


@dataclass(frozen=True)
class PluginDescriptor:
    """Presentation metadata for one available plugin."""

    plugin_id: str
    description: str
    entrypoint: str
    config: dict[str, Any]
    default: bool = False
    revision: str | None = None
    filename: str | None = None

    def to_dict(self, *, active_ids: Sequence[str] = ()) -> dict[str, Any]:
        payload = {
            "id": self.plugin_id,
            "name": self.plugin_id,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "config": _json_safe(self.config),
            "default": self.default,
            "active": self.plugin_id in active_ids,
        }
        if self.revision is not None:
            payload.update(revision=self.revision, filename=self.filename)
        return payload


@dataclass(frozen=True)
class PluginCatalog:
    """One step's live resolver, in display order, and its current digest."""

    step: str
    resolver: LocalPluginCatalog
    defaults: tuple[str, ...]

    @property
    def plugins(self) -> tuple[PluginDescriptor, ...]:
        definitions = {item.plugin_id: item for item in self.resolver.list(self.step)}
        ids = list(dict.fromkeys([*self.defaults, *sorted(definitions)]))
        return tuple(
            PluginDescriptor(
                plugin_id=item.plugin_id,
                description=str(item.metadata.get("description") or ""),
                entrypoint=item.entrypoint,
                config=dict(item.config),
                default=item.plugin_id in self.defaults,
                revision=item.metadata.get("revision"),
                filename=item.metadata.get("filename"),
            )
            for plugin_id in ids if (item := definitions.get(plugin_id)) is not None
        )

    @property
    def digest(self) -> str:
        payload = [item.to_dict() for item in self.plugins]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

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
        displays frames, and the other steps keep their selections.
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
            definitions = self.resolver.list(self.step)
            return replace(
                step_selection(self.step, plugins=[]),
                plugins=tuple(active_ids),
                plugin_specs={item.plugin_id: item.executable_entrypoint for item in definitions},
                plugin_configs={item.plugin_id: dict(item.config) for item in definitions},
            )
        except ValueError as exc:
            raise PluginCatalogError(str(exc)) from exc

    def build(self, activation: StepActivation) -> Any:
        """Construct exactly the activation's plugins and configs, as the step's runner."""

        return step_runner(activation)


def packaged_plugin_catalog(step: str) -> PluginCatalog:
    """Every packaged plugin of ``step``; the step's default selection is preselected."""

    if step not in SELECTABLE_STEPS:
        raise PluginCatalogError(
            f"plugins can be selected for {', '.join(SELECTABLE_STEPS)}, not {step!r}"
        )
    default_ids = DEFAULT_STEP_PLUGINS[step]
    entries = step_plugins(step)
    definitions: dict[str, PluginDefinition] = {
        item.plugin_id: item
        for item in packaged_activation(step, []).plugin_manager().available
    }
    resolver = LocalPluginCatalog(
        replace(item, metadata={"description": str(entries[item.plugin_id].get("description") or "")})
        for item in definitions.values()
    )
    return PluginCatalog(step=step, resolver=resolver, defaults=tuple(default_ids))


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
    "step_selection",
]
