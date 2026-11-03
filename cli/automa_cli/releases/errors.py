"""Errors raised when a vehicle runtime or its controller release cannot be used."""

from __future__ import annotations


class RuntimeHostError(RuntimeError):
    """A vehicle runtime is unreachable; the message names what to run next."""
