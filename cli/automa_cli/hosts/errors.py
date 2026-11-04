"""Errors raised when a host cannot be used."""

from __future__ import annotations


class RuntimeHostError(RuntimeError):
    """A host cannot be found, staged, reached, or started; the message names what to run next."""
