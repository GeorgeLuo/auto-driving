"""Memory presets: named, ordered selections of catalog plugins.

A preset lists the plugins to run and may override config for some of them.
The default is a preset. An activation built from one records the preset name
in its ``preset`` metadata key; a selection that is not a preset records
``custom``.
"""

from __future__ import annotations

from typing import Any


DEFAULT_MEMORY_PRESET = "recency_ledger"

MEMORY_PRESETS: dict[str, dict[str, Any]] = {
    "recency_ledger": {
        "description": (
            "Bounded recency ledger of observation things and signals, with "
            "age expiry and oldest-first eviction."
        ),
        "plugins": ["bounded_evidence"],
    },
    "chase-v1-detect-by-color": {
        "description": (
            "Chase v1: learn how near an obstacle ahead may come before "
            "avoidance starts, from collisions and from each stop."
        ),
        "plugins": ["stop_row"],
    },
}


def available_memory_preset_ids() -> tuple[str, ...]:
    return tuple(sorted(MEMORY_PRESETS))


DEFAULT_MEMORY_PLUGINS: tuple[str, ...] = tuple(
    MEMORY_PRESETS[DEFAULT_MEMORY_PRESET]["plugins"]
)
