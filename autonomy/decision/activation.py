"""Memory activation documents.

Defined in ``autonomy.decision_cycle.memory.activation`` and
``autonomy.decision_cycle.memory.snapshots.fallback``. Names imported here are
those objects. ``ActivatedMemoryStep`` is ``PluginMemoryRunner``.
"""

from autonomy.decision_cycle.memory.activation import (
    DEFAULT_EVICTION_POLICY,
    DEFAULT_MAX_AGE_MS,
    DEFAULT_MAX_RECORDS,
    MEMORY_ACTIVATION_SCHEMA,
    MemoryActivation,
    bounds_from_config,
    instantiate_memory_implementation,
    json_load_object,
    load_memory_implementation,
    load_memory_step_if_present,
    memory_manager_from_activation,
    memory_selection_config,
    read_memory_activation,
)
from autonomy.decision_cycle.memory.snapshots.fallback import (
    FALLBACK_COUNTER_WIDTH,
    FRAMEWORK_FALLBACK_IMPLEMENTATION_ID,
    MAX_FALLBACK_TIMESTAMP_MS,
    build_minimal_framework_fallback,
    framework_error_identity,
    framework_fallback_timestamp_ms,
    framework_reset_identity,
    validate_framework_fallback_capacity,
)
from autonomy.decision_cycle.memory.plugin_runner import PluginMemoryRunner as ActivatedMemoryStep

__all__ = [
    "DEFAULT_EVICTION_POLICY",
    "DEFAULT_MAX_AGE_MS",
    "DEFAULT_MAX_RECORDS",
    "FALLBACK_COUNTER_WIDTH",
    "FRAMEWORK_FALLBACK_IMPLEMENTATION_ID",
    "MAX_FALLBACK_TIMESTAMP_MS",
    "MEMORY_ACTIVATION_SCHEMA",
    "ActivatedMemoryStep",
    "MemoryActivation",
    "bounds_from_config",
    "build_minimal_framework_fallback",
    "framework_error_identity",
    "framework_fallback_timestamp_ms",
    "framework_reset_identity",
    "instantiate_memory_implementation",
    "json_load_object",
    "load_memory_implementation",
    "load_memory_step_if_present",
    "memory_manager_from_activation",
    "memory_selection_config",
    "read_memory_activation",
    "validate_framework_fallback_capacity",
]
