from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.perception import PerceptionText
from autonomy.vehicle import SensorSnapshot


OBSERVATION_SCHEMA = "decision_observation_v1"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class Observation:
    """Decision-facing representation of current sensory evidence."""

    observation_id: str
    created_at_ms: int
    sensor_snapshot: dict[str, Any]
    perception_schema: str | None = None
    perception_plugin_id: str | None = None
    summary: tuple[str, ...] = ()
    things: tuple[dict[str, Any], ...] = ()
    signals: tuple[dict[str, Any], ...] = ()
    artifacts: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = OBSERVATION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def observation_from_perception(
    *,
    observation_id: str,
    sensor_snapshot: SensorSnapshot | None,
    perception: PerceptionText | None,
    metadata: dict[str, Any] | None = None,
    created_at_ms: int | None = None,
) -> Observation:
    """Adapt perception evidence into the stable decision observation shape."""

    snapshot_dict = sensor_snapshot.to_dict() if sensor_snapshot is not None else {}
    if perception is None:
        return Observation(
            observation_id=observation_id,
            created_at_ms=created_at_ms or timestamp_ms(),
            sensor_snapshot=snapshot_dict,
            summary=("observation_available=false reason=no_perception",),
            metadata=metadata or {},
        )

    summary = tuple(perception.lines[:12])
    return Observation(
        observation_id=observation_id,
        created_at_ms=created_at_ms or timestamp_ms(),
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
