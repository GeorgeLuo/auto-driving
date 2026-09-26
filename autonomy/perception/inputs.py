from __future__ import annotations

from pathlib import Path
from typing import Any

from autonomy.memory import SharedMemory
from autonomy.vehicle import SensorSnapshot

from .interface import PerceptionRequest


def build_perception_request(
    snapshot: SensorSnapshot,
    *,
    output_dir: Path | None = None,
    metadata: dict[str, Any] | None = None,
    shared_memory: SharedMemory | None = None,
) -> PerceptionRequest:
    """Wrap a sensor snapshot without assuming which components plugins need."""

    return PerceptionRequest(
        snapshot=snapshot,
        output_dir=output_dir,
        metadata=dict(metadata or {}),
        shared_memory=shared_memory,
    )
