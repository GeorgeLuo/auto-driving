"""Narrow memory implementation shape used by the decision cycle.

Concrete reducers live under implementations/. The stable contract is only
update, reset, and snapshot. Framework code owns activation loading, timing,
status, and failure isolation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .cycle import DecisionFrameContext
from .memory import MemorySnapshot
from .observation import Observation


@runtime_checkable
class MemoryImplementation(Protocol):
    """Loadable memory reducer with explicit lifecycle methods."""

    implementation_id: str

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> MemorySnapshot:
        """Ingest one observation and return the detached retained state."""

    def reset(self) -> MemorySnapshot:
        """Begin a new lifecycle epoch and return an empty or cleared snapshot."""

    def snapshot(self) -> MemorySnapshot:
        """Return the latest detached state without applying a new observation."""
