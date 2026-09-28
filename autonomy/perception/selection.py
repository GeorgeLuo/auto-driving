"""Adapt perception mapper configuration to common plugin management."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autonomy.plugins import LocalPluginCatalog, PluginDefinition, PluginManager


def perception_plugin_manager(
    specs: Mapping[str, str],
    configs: Mapping[str, Mapping[str, Any]] | None = None,
) -> PluginManager:
    configs = configs or {}
    catalog = LocalPluginCatalog(
        PluginDefinition(
            step="perception",
            plugin_id=plugin_id,
            entrypoint=spec,
            config=configs.get(plugin_id, {}),
        )
        for plugin_id, spec in specs.items()
    )
    return PluginManager("perception", catalog)
