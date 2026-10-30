"""Deployment results that cross from an operation to its presentation.

A receipt is the JSON body of a plan or a completed deployment. Facts that
the text needs and the JSON body does not carry, such as whether a local host
process was already running, stay beside the receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..runs.models import StopResult


@dataclass(frozen=True)
class DeploymentResult:
    exit_code: int
    message: str = ""
    receipt: dict[str, Any] | None = None
    host_present: bool | None = None
    render_kind: str = "generic"
    child_render_kind: str = "generic"

    stop: StopResult | None = None
    deleted_path: Path | None = None
    host_name: str | None = None
    stopped_run: bool = False
    local: bool = True
