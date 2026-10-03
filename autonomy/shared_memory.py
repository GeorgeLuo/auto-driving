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
JSON. A perception plugin's ``memory_required`` flag means that plugin needs
this map.

Perception, memory, and proposal plugins all read and write this map. Which
keys a plugin reads and where it publishes are the plugin's choice; plugins
that exchange values agree on the key (for example, memory plugins that retain
evidence publish records a proposal plugin reads). A memory plugin may publish
a current-cycle Observation at ``OBSERVATION_KEY`` (in ``memory.publication``)
for subsequent steps.
"""

from collections.abc import MutableMapping
from typing import Any

SharedMemory = MutableMapping[str, Any]

__all__ = ["SharedMemory"]
