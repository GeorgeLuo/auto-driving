"""Memory plugin contract used by the cycle's ``remember`` operation.

Concrete plugins live under implementations/. Like perception plugins, a
memory plugin keeps the history later frames need in the host map,
``context.shared_memory``, under keys it owns, and chooses where to publish
anything other plugins read. ``update`` performs remember; ``reset`` starts a
new epoch and writes the plugin's fresh state to the map. ``status`` is an
optional JSON summary of that state for diagnostics. Framework code owns
selection, timing, and failure isolation.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory


@runtime_checkable
class MemoryImplementation(Protocol):
    """Loadable memory plugin. ``update`` performs ``remember``."""

    implementation_id: str

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> None:
        """Remember one observation in ``context.shared_memory``; raise on failure."""

    def reset(self, shared_memory: SharedMemory) -> None:
        """Begin a new epoch: replace this plugin's keys with fresh state."""


def plugin_status(implementation: Any, shared_memory: SharedMemory | None) -> dict[str, Any] | None:
    """Return the plugin's optional ``status(shared_memory)`` summary, if it has one."""

    status = getattr(implementation, "status", None)
    if not callable(status):
        return None
    return status(shared_memory)
