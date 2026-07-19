"""Explicit selection catalog for packaged memory implementations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_MEMORY_IMPLEMENTATION = "bounded_evidence"

MEMORY_IMPLEMENTATIONS: dict[str, dict[str, Any]] = {
    "bounded_evidence": {
        "implementation_id": "bounded_evidence",
        "implementation_spec": (
            "implementations.memory.bounded_evidence:BoundedEvidenceLedger"
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
        },
    },
}


def available_memory_implementation_ids() -> tuple[str, ...]:
    return tuple(sorted(MEMORY_IMPLEMENTATIONS))


def memory_implementation_spec(implementation_id: str) -> dict[str, Any]:
    try:
        entry = MEMORY_IMPLEMENTATIONS[implementation_id]
    except KeyError as exc:
        known = ", ".join(available_memory_implementation_ids()) or "(none)"
        raise KeyError(
            f"unknown memory implementation {implementation_id!r}; known: {known}"
        ) from exc
    return {
        "implementation_id": entry["implementation_id"],
        "implementation_spec": entry["implementation_spec"],
        "description": entry["description"],
        "default_config": deepcopy(entry["default_config"]),
    }


def build_memory_activation_payload(
    implementation_id: str = DEFAULT_MEMORY_IMPLEMENTATION,
    *,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a current-schema memory activation document payload."""

    entry = memory_implementation_spec(implementation_id)
    config = deepcopy(entry["default_config"])
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
