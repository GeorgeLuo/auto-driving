"""Deployment results that cross from an operation to its presentation.

Each operation returns its own result, with a ``kind`` naming the outcome. A
receipt is the JSON body of a plan or a completed deployment. Facts that the
text needs and the JSON body does not carry, such as whether a local host
process was already running, stay beside the receipt. ``message`` is the
sentence an ``error`` shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..runs.models import StopResult


@dataclass(frozen=True)
class CoreResult:
    """Installing the PiCar core bundle: its ``plan``, ``synced``, or an ``error``."""

    exit_code: int
    kind: Literal["plan", "synced", "error"]
    message: str = ""
    receipt: dict[str, Any] | None = None


@dataclass(frozen=True)
class UpdateResult:
    """Packaging and deploying a host's release, or its plan.

    ``local`` is a host this machine runs and ``remote`` a PiCar. ``receipt``
    is a provider's receipt with no formatter of its own; text prints it as
    JSON.
    """

    exit_code: int
    kind: Literal["local", "remote", "receipt", "error"]
    message: str = ""
    receipt: dict[str, Any] | None = None
    host_present: bool | None = None


@dataclass(frozen=True)
class CreateResult:
    """Creating a host, or its plan.

    A ``plan`` has the release plan and, for a PiCar, the core plan. ``created``
    has the release that was deployed and the running host's state, if it has
    one. The renderer assembles the combined receipt from these parts.
    """

    exit_code: int
    kind: Literal["plan", "created", "error"]
    message: str = ""
    configuration: dict[str, Any] | None = None
    core: CoreResult | None = None
    release: UpdateResult | None = None
    host_state: dict[str, Any] | None = None


@dataclass(frozen=True)
class DeleteResult:
    """Deleting a host: ``deleted``, a remote stop that failed, a run that would not stop, or an ``error``."""

    exit_code: int
    kind: Literal["deleted", "delete_failed", "stop_failed", "error"]
    message: str = ""
    host_name: str = ""
    stop: StopResult | None = None
    deleted_path: Path | None = None
    stopped_run: bool = False
    local: bool = True
