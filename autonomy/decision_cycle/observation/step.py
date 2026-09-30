"""Default ``observe`` step.

It adapts perception evidence and the sensor snapshot into an ``Observation``.
The cycle's ``observe`` callback can replace it.
"""

from __future__ import annotations

import time
from typing import Any

from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.vehicle import SensorSnapshot


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def observation_from_perception(
    *,
    observation_id: str,
    sensor_snapshot: SensorSnapshot | None,
    perception: PerceptionText | None,
    metadata: dict[str, Any] | None = None,
    created_at_ms: int | None = None,
) -> Observation:
    """Default ``observe`` step: perception evidence plus the sensor snapshot."""

    snapshot_dict = sensor_snapshot.to_dict() if sensor_snapshot is not None else {}
    observation_created_at_ms = (
        timestamp_ms() if created_at_ms is None else created_at_ms
    )
    if perception is None:
        return Observation(
            observation_id=observation_id,
            created_at_ms=observation_created_at_ms,
            sensor_snapshot=snapshot_dict,
            summary=("observation_available=false reason=no_perception",),
            metadata=metadata or {},
        )

    summary = tuple(perception.lines[:12])
    return Observation(
        observation_id=observation_id,
        created_at_ms=observation_created_at_ms,
        sensor_snapshot=snapshot_dict,
        perception_schema=perception.schema,
        perception_plugin_id=perception.plugin_id,
        summary=summary,
        things=tuple(thing.to_dict() for thing in perception.things),
        signals=tuple(signal.to_dict() for signal in perception.signals),
        artifacts=dict(perception.artifacts),
        metadata={
            "limits": list(perception.limits),
            **(metadata or {}),
        },
    )
