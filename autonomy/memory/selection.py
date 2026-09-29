"""Build the memory-scoped view of the common plugin manager."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autonomy.plugins import PluginManager


def memory_plugin_manager(
    specs: Mapping[str, str],
    configs: Mapping[str, Mapping[str, Any]] | None = None,
) -> PluginManager:
    """Create a memory manager without selecting or constructing plugins."""

    return PluginManager.from_specs("memory", specs, configs)
