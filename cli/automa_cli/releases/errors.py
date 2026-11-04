"""Errors raised when a controller release cannot be used."""

from __future__ import annotations


class ReleaseError(RuntimeError):
    """A controller release is missing, unreadable, or does not match; the message names the update to run."""
