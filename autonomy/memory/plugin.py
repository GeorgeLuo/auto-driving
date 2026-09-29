"""Retained-evidence reducer used by the cycle's ``remember`` operation.

Concrete reducers live under implementations/. ``update`` performs remember
and returns a ``MemorySnapshot``. The stable contract is only update, reset,
and snapshot. Framework code owns activation loading, timing, status, and
failure isolation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autonomy.decision.cycle import DecisionFrameContext
from autonomy.decision.observation import Observation
from autonomy.memory.values import MemorySnapshot
from autonomy.shared_memory import SharedMemory


@runtime_checkable
class MemoryImplementation(Protocol):
    """Loadable retained-evidence reducer. ``update`` performs ``remember``."""

    implementation_id: str

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> MemorySnapshot:
        """Remember one observation and return detached retained evidence."""

    def reset(self, shared_memory: SharedMemory | None = None) -> MemorySnapshot:
        """Begin a new epoch. Reducers that keep history use the host map."""

    def snapshot(self) -> MemorySnapshot:
        """Return detached retained evidence, including the initial empty snapshot."""
