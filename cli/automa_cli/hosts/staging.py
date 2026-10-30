"""Staged host configuration and the local runtime directory it uses.

Finding a running endpoint is ``discovery``. Starting the Chase host process
is ``local``. Step activations stay in ``step_activations``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..releases.errors import RuntimeHostError
from ..releases.paths import bundle_root
from ..shared.paths import RUNTIME_ROOT
from .step_activations import staging_host


def automation_dir(host_name: str) -> Path:
    """The vehicle's local runtime directory: run state, records, host, and view."""

    return bundle_root(host_name) / "runtime" / "automation"


def staged_host(host_name: str) -> dict[str, Any]:
    vehicle, error = staging_host(host_name, runtime_root=RUNTIME_ROOT)
    if vehicle is None:
        raise RuntimeHostError(error or f"Host {host_name!r} is not staged.")
    return vehicle


def staged_connection(host_name: str) -> dict[str, Any]:
    """The connection ``hosts create`` recorded for the vehicle, without discovery."""

    vehicle = staged_host(host_name)
    connection = vehicle.get("connection")
    return connection if isinstance(connection, dict) else {}
