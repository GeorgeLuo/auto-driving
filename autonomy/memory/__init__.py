"""Host-owned shared memory for sequential steps and plugins.

A host supplies one mutable mapping for a run; absent access is None. Parameters
and fields that carry this mapping are named shared_memory. Producers own their
keys and include timing in values where needed. Writes are visible to subsequent
consumers in execution order. Hosts own reset and synchronization; this mapping
imposes no retention policy or domain-specific schema.

Plugins must be replaceable between frames without changing results. All
history affecting later frames belongs here, including previous image buffers,
identity allocation, counters, and smoothing state. Configuration and reusable
model resources may remain on instances. Plugins own namespacing, bounds,
discontinuity handling, and when a completed update is committed. Hosts pass
one map in execution order and clear/replace it at run boundaries; they do not
interpret plugin histories. Values may be Python records or arrays, not only
JSON. A memory snapshot is a plugin's evidence view, not the entire shared map.

Memory implementations may publish their retained evidence at
"decision.snapshot". The decision cycle reads the memory step's result but
does not write that key. A memory plugin may publish a current-cycle Observation
at "decision.observation" for subsequent steps. Other producers may use their
own keys; the existing evidence reducer does not evict those entries.
"""

from collections.abc import MutableMapping
from importlib import import_module
from typing import Any

SharedMemory = MutableMapping[str, Any]


_ACTIVATION_EXPORTS = (
    "MEMORY_ACTIVATION_SCHEMA",
    "ActivatedMemoryStep",
    "MemoryActivation",
    "instantiate_memory_implementation",
    "load_memory_implementation",
    "load_memory_step_if_present",
    "read_memory_activation",
)

__all__ = ["SharedMemory", *_ACTIVATION_EXPORTS]


def __getattr__(name: str) -> Any:
    """Load activation APIs lazily to keep decision and memory contracts acyclic."""
    if name not in _ACTIVATION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    activation = import_module(".activation", __name__)
    value = getattr(activation, name)
    globals()[name] = value
    return value
