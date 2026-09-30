"""Observation-to-retained-evidence contracts.

``remember`` is the cycle operation. A reducer's ``update`` performs it and
returns a ``MemorySnapshot``. ``SharedMemory`` is the host map defined in
``autonomy.shared_memory``, re-exported as the same object. The memory step
is defined under ``autonomy.decision_cycle.memory``; names imported here are
those objects.
"""

from autonomy.shared_memory import SharedMemory

from autonomy.decision_cycle.memory.snapshots.values import (
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
    detach_memory_snapshot,
    empty_memory_snapshot,
    error_memory_snapshot,
    serialized_mapping_bytes,
    serialized_memory_snapshot_bytes,
    unavailable_memory_snapshot,
)
from autonomy.decision_cycle.memory.plugin import MemoryImplementation
from autonomy.decision_cycle.memory.activation import (
    MEMORY_ACTIVATION_SCHEMA,
    MemoryActivation,
    instantiate_memory_implementation,
    load_memory_implementation,
    load_memory_step_if_present,
    read_memory_activation,
)
from autonomy.decision_cycle.memory.plugin_runner import PluginMemoryRunner
from autonomy.serialization import (
    canonical_json_bytes,
    canonical_json_utf8,
    ensure_strict_json_value,
)

ActivatedMemoryStep = PluginMemoryRunner

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
