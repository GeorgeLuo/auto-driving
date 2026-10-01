"""Packaged memory plugins and the memory step's default selection."""

from __future__ import annotations

from typing import Any

DEFAULT_MEMORY_PLUGIN = "bounded_evidence"

MEMORY_PLUGINS: dict[str, dict[str, Any]] = {
    "bounded_evidence": {
        "spec": (
            "implementations.decision_cycle.memory.bounded_evidence.plugin:BoundedEvidenceLedger"
        ),
        "description": (
            "Bounded recency ledger of observation things and signals with "
            "provenance, age expiry, and oldest-first eviction. Does not claim "
            "semantic object identity or world truth."
        ),
        "default_config": {
            "max_records": 32,
            "max_age_ms": 10_000,
            "eviction_policy": "oldest_first",
            "min_confidence": 0.0,
            "retain_things": True,
            "retain_signals": True,
            "max_property_bytes": 4_096,
            "max_serialized_bytes": 262_144,
        },
    },
}
MEMORY_PLUGINS["multi_obstruction_tracks"] = {
    "spec": (
        "implementations.decision_cycle.memory.multi_obstruction_tracks.plugin:MultiObstructionMemory"
    ),
    "description": (
        "Associates obstruction candidates from the multi_obstruction_tracks perception "
        "plugin into tracks across frames, with optical-flow history, lost-track handling, "
        "and a bounded evidence ledger."
    ),
    "default_config": dict(MEMORY_PLUGINS[DEFAULT_MEMORY_PLUGIN]["default_config"]),
}
DEFAULT_MEMORY_PLUGINS: tuple[str, ...] = (DEFAULT_MEMORY_PLUGIN,)
