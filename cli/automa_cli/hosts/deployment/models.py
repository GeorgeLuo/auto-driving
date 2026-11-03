"""Deployment results that cross from an operation to its presentation.

A receipt is the JSON body of a plan or a completed deployment. Facts that
the text needs and the JSON body does not carry, such as whether a local host
process was already running, stay beside the receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeploymentResult:
    exit_code: int
    message: str = ""
    receipt: dict[str, Any] | None = None
    host_present: bool | None = None
