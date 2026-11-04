"""Plugin definitions and display ordering shared by CLI catalog views."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from autonomy.plugins import PluginDefinition
from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS, step_plugins


def ordered_plugin_definitions(
    definitions: Iterable[PluginDefinition], defaults: Sequence[str],
) -> tuple[PluginDefinition, ...]:
    """Defaults in selection order, then the remaining definitions by ID."""

    by_id = {item.plugin_id: item for item in definitions}
    ids = dict.fromkeys([*defaults, *sorted(by_id)])
    return tuple(by_id[plugin_id] for plugin_id in ids if plugin_id in by_id)


def packaged_plugin_definitions(step: str) -> tuple[PluginDefinition, ...]:
    """The step's packaged definitions with descriptions, defaults first."""

    definitions = (
        PluginDefinition(step, plugin_id, entry["spec"], entry["default_config"],
                         {"description": str(entry.get("description") or "")})
        for plugin_id, entry in step_plugins(step).items()
    )
    return ordered_plugin_definitions(definitions, DEFAULT_STEP_PLUGINS[step])
