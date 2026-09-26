"""Host-owned shared memory for sequential stages and plugins.

A host supplies one mutable mapping for a run; absent access is None. Producers
own their keys and include timing in values where needed. Writes are visible to
subsequent consumers in execution order. Hosts own reset and synchronization;
this mapping imposes no retention policy or domain-specific schema.

Memory implementations may publish their retained evidence at
"decision.snapshot". The decision cycle reads the memory stage's result but
does not write that key. A memory plugin may publish a current-cycle Observation
at "decision.observation" for subsequent stages. Other producers may use their
own keys; the existing evidence reducer does not evict those entries.
"""

from collections.abc import MutableMapping
from typing import Any

SharedMemory = MutableMapping[str, Any]
