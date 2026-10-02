from __future__ import annotations

from pathlib import Path
from typing import Any

from autonomy.shared_memory import SharedMemory
from autonomy.vehicle import SensorFrame

from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest


def build_perception_request(
    sensor_frame: SensorFrame,
    *,
    output_dir: Path | None = None,
    metadata: dict[str, Any] | None = None,
    shared_memory: SharedMemory | None = None,
) -> PerceptionRequest:
    """Wrap a sensor frame without assuming which feeds plugins need."""

    return PerceptionRequest(
        sensor_frame=sensor_frame,
        output_dir=output_dir,
        metadata=dict(metadata or {}),
        shared_memory=shared_memory,
    )
