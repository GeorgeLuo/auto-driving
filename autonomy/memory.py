"""Host-owned shared memory for sequential stages and plugins.

A host supplies one mutable mapping for a run; absent access is None. Producers
own their keys and include timing in values where needed. Writes are visible to
subsequent consumers in execution order. Hosts own reset and synchronization;
this mapping imposes no retention policy or domain-specific schema.

Plugins must be replaceable between frames without changing results. All
history affecting later frames belongs here, including previous image buffers,
identity allocation, counters, and smoothing state. Configuration and reusable
model resources may remain on instances. Plugins own namespacing, bounds,
discontinuity handling, and when a completed update is committed. Hosts pass
one map in execution order and clear/replace it at run boundaries; they do not
interpret plugin histories. Values may be Python records or arrays, not only
JSON. A memory snapshot is a plugin's evidence view, not the entire shared map.

Memory implementations may publish their retained evidence at
"decision.snapshot". The decision cycle reads the memory stage's result but
does not write that key. A memory plugin may publish a current-cycle Observation
at "decision.observation" for subsequent stages. Other producers may use their
own keys; the existing evidence reducer does not evict those entries.
"""

from collections.abc import MutableMapping
from typing import Any

SharedMemory = MutableMapping[str, Any]
