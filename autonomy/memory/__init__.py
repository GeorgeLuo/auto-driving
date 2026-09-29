"""Memory-step activation, selection, runner, protocol, and value contracts.

``SharedMemory`` is the host map defined in ``autonomy.shared_memory``,
re-exported as the same object. Runner, protocol, and activation load on first
use because they import the decision cycle, which imports these values.
"""

from importlib import import_module
from typing import Any

from autonomy.shared_memory import SharedMemory

from .values import (
    DEFAULT_MAX_DIAGNOSTIC_CHARS,
    DEFAULT_MAX_PROPERTY_BYTES,
    DEFAULT_MAX_SERIALIZED_BYTES,
    MEMORY_HEALTH_VALUES,
    MEMORY_SNAPSHOT_SCHEMA,
    MIN_MAX_SERIALIZED_BYTES,
    MemoryBounds,
    MemoryHealth,
    MemoryProvenance,
    MemorySnapshot,
    RetainedEvidence,
    canonical_json_bytes,
    canonical_json_utf8,
    detach_memory_snapshot,
    empty_memory_snapshot,
    ensure_strict_json_value,
    error_memory_snapshot,
    serialized_mapping_bytes,
    serialized_memory_snapshot_bytes,
    unavailable_memory_snapshot,
)

_LAZY_EXPORTS = {
    "MEMORY_ACTIVATION_SCHEMA": "activation",
    "ActivatedMemoryStep": "activation",
    "MemoryActivation": "activation",
    "MemoryImplementation": "plugin",
    "PluginMemoryRunner": "plugin_runner",
    "instantiate_memory_implementation": "activation",
    "load_memory_implementation": "activation",
    "load_memory_step_if_present": "activation",
    "read_memory_activation": "activation",
}

__all__ = [
    "SharedMemory",
    "DEFAULT_MAX_DIAGNOSTIC_CHARS",
    "DEFAULT_MAX_PROPERTY_BYTES",
    "DEFAULT_MAX_SERIALIZED_BYTES",
    "MEMORY_ACTIVATION_SCHEMA",
    "MEMORY_HEALTH_VALUES",
    "MEMORY_SNAPSHOT_SCHEMA",
    "MIN_MAX_SERIALIZED_BYTES",
    "ActivatedMemoryStep",
    "MemoryActivation",
    "MemoryBounds",
    "MemoryHealth",
    "MemoryImplementation",
    "MemoryProvenance",
    "MemorySnapshot",
    "PluginMemoryRunner",
    "RetainedEvidence",
    "canonical_json_bytes",
    "canonical_json_utf8",
    "detach_memory_snapshot",
    "empty_memory_snapshot",
    "ensure_strict_json_value",
    "error_memory_snapshot",
    "instantiate_memory_implementation",
    "load_memory_implementation",
    "load_memory_step_if_present",
    "read_memory_activation",
    "serialized_mapping_bytes",
    "serialized_memory_snapshot_bytes",
    "unavailable_memory_snapshot",
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
