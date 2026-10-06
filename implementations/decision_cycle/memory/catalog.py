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
)
