"""Host-owned shared memory for sequential stages and plugins.

A host supplies one mutable mapping for a run; absent access is None. Producers
own their keys and include timing in values where needed. Writes are visible to
subsequent consumers in execution order. Hosts own reset and synchronization;
this mapping imposes no retention policy or domain-specific schema.

Workbench publishes its existing MemorySnapshot at "decision.snapshot" after
its memory stage. That entry is owned by the memory stage. Other producers may
use their own keys; the existing evidence reducer does not evict those entries.
"""

from collections.abc import MutableMapping
from typing import Any

SharedMemory = MutableMapping[str, Any]
