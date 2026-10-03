"""Packaged memory plugins."""

from __future__ import annotations

from typing import Any

_BOUNDED_EVIDENCE_CONFIG: dict[str, Any] = {
    "max_records": 32,
    "max_age_ms": 10_000,
    "eviction_policy": "oldest_first",
    "min_confidence": 0.0,
    "retain_things": True,
    "retain_signals": True,
    "max_property_bytes": 4_096,
    "max_serialized_bytes": 262_144,
}

# Each plugin declares its own ID (its ``plugin_id``); entries do not repeat it.
# ``perception_plugins`` (optional) names the perception plugins whose output the
# plugin reads. The workbench lists it; nothing checks the two selections against it.
MEMORY_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "spec": (
            "implementations.decision_cycle.memory.plugins.bounded_evidence.plugin:BoundedEvidenceLedger"
        ),
        "description": (
            "Bounded recency ledger of observation things and signals with "
            "origin, age expiry, and oldest-first eviction. Does not claim "
            "semantic object identity or world truth."
        ),
        "default_config": _BOUNDED_EVIDENCE_CONFIG,
    },
    {
        "spec": (
            "implementations.decision_cycle.memory.plugins.multi_obstruction_tracks.plugin:"
            "MultiObstructionMemory"
        ),
        "description": (
            "Associates obstruction candidates from the multi_obstruction_tracks perception "
            "plugin into tracks across frames, with optical-flow history, lost-track handling, "
            "and a bounded evidence ledger."
        ),
        "perception_plugins": ("multi_obstruction_tracks",),
        "default_config": dict(_BOUNDED_EVIDENCE_CONFIG),
    },
)
