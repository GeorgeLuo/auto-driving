"""Explicit selection catalog for packaged memory implementations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from autonomy.plugins import LocalPluginCatalog, PluginDefinition, PluginManager


DEFAULT_MEMORY_IMPLEMENTATION = "bounded_evidence"

MEMORY_PLUGIN_CATALOG = LocalPluginCatalog([
    PluginDefinition(
        step="memory",
        plugin_id="bounded_evidence",
        entrypoint=(
            "implementations.memory.bounded_evidence:BoundedEvidenceLedger"
        ),
        metadata={"description": (
            "Bounded recency ledger of observation things and signals with "
            "provenance, age expiry, and oldest-first eviction. Does not claim "
            "semantic object identity or world truth."
        )},
        config={
            "max_records": 32,
            "max_age_ms": 10_000,
            "eviction_policy": "oldest_first",
            "min_confidence": 0.0,
            "retain_things": True,
            "retain_signals": True,
            "max_property_bytes": 4_096,
            "max_serialized_bytes": 262_144,
        },
    ),
])


def available_memory_implementation_ids() -> tuple[str, ...]:
    return tuple(plugin.plugin_id for plugin in MEMORY_PLUGIN_CATALOG.list("memory"))


def memory_implementation_spec(implementation_id: str) -> dict[str, Any]:
    # Adapt the current single-implementation activation to core selection.
    entry, = PluginManager("memory", MEMORY_PLUGIN_CATALOG).select([implementation_id])
    return {
        "implementation_id": entry.plugin_id,
        "implementation_spec": entry.entrypoint,
        "description": entry.metadata.get("description", ""),
        "default_config": deepcopy(dict(entry.config)),
    }


def build_memory_activation_payload(
    implementation_id: str = DEFAULT_MEMORY_IMPLEMENTATION,
    *,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a current-schema memory activation document payload."""

    entry = memory_implementation_spec(implementation_id)
    config = entry["default_config"]
    if config_overrides:
        config.update(config_overrides)
    return {
        "schema": "automa_memory_activation_v0",
        "memory": {
            "implementation_id": entry["implementation_id"],
            "implementation_spec": entry["implementation_spec"],
            "implementation_config": config,
        },
    }
