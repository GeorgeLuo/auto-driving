"""Live catalog listing and selection for the replay workbench.

The workbench offers every packaged plugin of the ``WORKBENCH_SELECTABLE_STEPS``:
perception, memory, proposal and plan. A selection is built by the catalog's
``selection_activation``, the activation the CLI stages for that step, so a
preset given on the command line keeps its plugin configs. Listing a catalog
does not construct plugins; construction happens only after the operator
selects plugins for a replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Sequence

from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.steps import ONE_PLUGIN_STEPS, step_runner
from autonomy.plugins import LocalPluginCatalog
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PLUGINS,
    PRESET_STEPS,
    plugin_list_preset,
    selection_activation,
)

from ..shared.plugin_catalog import ordered_plugin_definitions, packaged_plugin_definitions


# The workbench exposes selection controls for the steps with packaged presets.
WORKBENCH_SELECTABLE_STEPS = PRESET_STEPS

PLUGIN_CATALOG_SCHEMA = "workbench_plugin_catalog_v1"


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
            for item in ordered_plugin_definitions(self.resolver.list(self.step), self.defaults)
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
            "single_plugin": self.step in ONE_PLUGIN_STEPS,
            "plugins": [item.to_dict(active_ids=active_ids) for item in self.plugins],
        }

    def normalize_selection(self, active_ids: Sequence[str] | None) -> tuple[str, ...]:
        """Validate ids while preserving the given order.

        An empty selection disables plugins for a step that can be cleared.
        A one-plugin step requires exactly one id. Replay still displays
        frames, and the other steps keep their selections.
        """

        raw_values = [] if active_ids is None else list(active_ids)
        if any(not isinstance(value, str) for value in raw_values):
            raise PluginCatalogError("active_plugin_ids must contain non-empty strings")
        values = [value.strip() for value in raw_values]
        if any(not value for value in values):
            raise PluginCatalogError("active_plugin_ids must contain non-empty strings")
        if len(values) != len(set(values)):
            raise PluginCatalogError("active_plugin_ids must not contain duplicates")
        if self.step in ONE_PLUGIN_STEPS and len(values) != 1:
            raise PluginCatalogError(
                f"the {self.step} step runs exactly one plugin; {len(values)} selected"
            )
        return self.activation(values).plugins

    def activation(self, active_ids: Sequence[str]) -> StepActivation:
        """The CLI's activation for these ids; unknown ids are a catalog error."""

        try:
            definitions = self.resolver.list(self.step)
            return replace(
                selection_activation(self.step, plugins=[]),
                plugins=tuple(active_ids),
                plugin_specs={item.plugin_id: item.executable_entrypoint for item in definitions},
                plugin_configs={item.plugin_id: dict(item.config) for item in definitions if item.config},
                metadata={"preset": plugin_list_preset(self.step, active_ids)},
            )
        except ValueError as exc:
            raise PluginCatalogError(str(exc)) from exc

    def build(self, activation: StepActivation) -> Any:
        """Construct exactly the activation's plugins and configs, as the step's runner."""

        return step_runner(activation)


def packaged_plugin_catalog(step: str) -> PluginCatalog:
    """Every packaged plugin of ``step``; the step's default selection is preselected."""

    if step not in WORKBENCH_SELECTABLE_STEPS:
        raise PluginCatalogError(
            f"plugins can be selected for {', '.join(WORKBENCH_SELECTABLE_STEPS)}, not {step!r}"
        )
    resolver = LocalPluginCatalog(packaged_plugin_definitions(step))
    return PluginCatalog(step=step, resolver=resolver, defaults=tuple(DEFAULT_STEP_PLUGINS[step]))


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
    "WORKBENCH_SELECTABLE_STEPS",
    "packaged_plugin_catalog",
]
