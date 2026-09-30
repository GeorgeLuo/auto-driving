"""Memory failure that stops the current decision cycle."""

from __future__ import annotations


class MemoryUpdateError(RuntimeError):
    """A memory update failed, so this decision cycle cannot continue."""

    boundary = "memory"


__all__ = ["MemoryUpdateError"]
