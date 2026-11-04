"""Which deployment operations each provider supports.

A provider is selected by name. An operation that is absent is unsupported and
must fail before that operation changes a host. This module does not import a
deployment implementation; the service registers the implementations it owns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .models import CreateResult, DeleteResult, UpdateResult


@dataclass(frozen=True)
class DeploymentBackend:
    """The create, update, and delete operations one provider supports."""

    create: Callable[..., CreateResult] | None = None
    update: Callable[..., UpdateResult] | None = None
    delete: Callable[..., DeleteResult] | None = None


BACKENDS: dict[str, DeploymentBackend] = {}


def register_backend(provider: str, backend: DeploymentBackend) -> None:
    """Replace the operations registered for ``provider``."""

    BACKENDS[provider] = backend


def backend_for(provider: str) -> DeploymentBackend | None:
    """The operations registered for ``provider``, or none when it has no deployment."""

    return BACKENDS.get(provider)


def unsupported_operation(provider: str, operation: str) -> str:
    return f"Provider {provider!r} does not support hosts {operation}."
