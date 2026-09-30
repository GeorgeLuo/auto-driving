"""Build the proposal-scoped view of the common plugin manager and load its plugins.

A proposal engine config names its selection the way a memory activation does:
``plugins`` (the selected IDs, in order), ``plugin_specs`` (ID to
``module.path:Name`` entrypoint) and optional ``plugin_configs`` (ID to the
keyword arguments the entrypoint is constructed with). The selection has no
count limit; an empty selection yields an idle plan.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from autonomy.plugins import PluginDefinition, PluginManager, replace_selection

PROPOSAL_STEP = "proposal"
PROPOSAL_CONFIG_KEYS = ("plugins", "plugin_specs", "plugin_configs")


def proposal_plugin_manager(
    specs: Mapping[str, str],
    configs: Mapping[str, Mapping[str, Any]] | None = None,
) -> PluginManager:
    """Create a proposal manager without selecting or constructing plugins."""

    return PluginManager.from_specs(PROPOSAL_STEP, specs, configs)


def proposal_manager_from_config(engine_config: Mapping[str, Any]) -> PluginManager:
    """Resolve an engine config's proposal selection without loading plugins."""

    if not isinstance(engine_config, Mapping):
        raise TypeError("proposal engine config must be an object")
    unknown = sorted(set(engine_config) - set(PROPOSAL_CONFIG_KEYS))
    if unknown:
        raise ValueError(f"unknown proposal config keys: {', '.join(unknown)}")
    plugins = engine_config.get("plugins")
    specs = engine_config.get("plugin_specs")
    configs = engine_config.get("plugin_configs", {})
    if not isinstance(plugins, list) or not all(isinstance(item, str) for item in plugins):
        raise ValueError("proposal plugins must be a list of plugin IDs")
    if len(plugins) != len(set(plugins)):
        raise ValueError("proposal plugins must be unique")
    if not isinstance(specs, Mapping) or not isinstance(configs, Mapping):
        raise ValueError("proposal plugin_specs and plugin_configs must be objects")
    manager = proposal_plugin_manager(specs, configs)
    manager.select(plugins)
    return manager


def load_proposal_plugin(definition: PluginDefinition, *, reload_module: bool = False) -> Any:
    """Import a definition's entrypoint and construct it with the definition's config."""

    module_name, separator, attribute = definition.entrypoint.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("proposal plugin spec must be 'module.path:Name'")
    importlib.invalidate_caches()
    module = importlib.import_module(module_name)
    if reload_module:
        module = importlib.reload(module)
    factory = getattr(module, attribute)
    plugin = factory(**deepcopy(dict(definition.config)))
    if not callable(plugin):
        raise TypeError(f"proposal plugin is not callable: {definition.entrypoint}")
    # Proposals are admitted only under the ID they were selected by.
    if getattr(plugin, "plugin_id", None) != definition.plugin_id:
        raise TypeError(
            f"proposal plugin {definition.entrypoint} declares plugin_id "
            f"{getattr(plugin, 'plugin_id', None)!r}, selected as {definition.plugin_id!r}"
        )
    return plugin


def load_proposal_plugins(manager: PluginManager) -> dict[str, Any]:
    """Load the manager's selected plugins, keyed by plugin ID in selection order."""

    if manager.step != PROPOSAL_STEP:
        raise ValueError(f"manager belongs to {manager.step!r}, not {PROPOSAL_STEP!r}")
    instances = replace_selection((), manager.selected, load=load_proposal_plugin)
    return dict(zip(manager.selected_ids, instances))
