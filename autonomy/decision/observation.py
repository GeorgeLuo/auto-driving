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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Observation":
        """Rehydrate an Observation from a detached JSON-friendly mapping."""

        if not isinstance(data, dict):
            raise TypeError("observation payload must be a dictionary")
        observation_id = str(data.get("observation_id") or "").strip()
        if not observation_id:
            raise ValueError("observation requires observation_id")
        sensor_snapshot = data.get("sensor_snapshot")
        if sensor_snapshot is None:
            sensor_snapshot = {}
        if not isinstance(sensor_snapshot, dict):
            raise ValueError("observation sensor_snapshot must be a dictionary")
        things = data.get("things") or ()
        signals = data.get("signals") or ()
        if not isinstance(things, (list, tuple)):
            raise ValueError("observation things must be a list")
        if not isinstance(signals, (list, tuple)):
            raise ValueError("observation signals must be a list")
        artifacts = data.get("artifacts") or {}
        metadata = data.get("metadata") or {}
        if not isinstance(artifacts, dict):
            raise ValueError("observation artifacts must be a dictionary")
        if not isinstance(metadata, dict):
            raise ValueError("observation metadata must be a dictionary")
        summary = data.get("summary") or ()
        if isinstance(summary, str):
            summary_items = (summary,)
        elif isinstance(summary, (list, tuple)):
            summary_items = tuple(str(item) for item in summary)
        else:
            raise ValueError("observation summary must be a string or list")
        return cls(
            observation_id=observation_id,
            created_at_ms=int(data.get("created_at_ms") or 0),
            sensor_snapshot=dict(sensor_snapshot),
            perception_schema=(
                str(data["perception_schema"])
                if data.get("perception_schema") is not None
                else None
            ),
            perception_plugin_id=(
                str(data["perception_plugin_id"])
                if data.get("perception_plugin_id") is not None
                else None
            ),
            summary=summary_items,
            things=tuple(item for item in things if isinstance(item, dict)),
            signals=tuple(item for item in signals if isinstance(item, dict)),
            artifacts={str(key): str(value) for key, value in artifacts.items()},
            metadata=dict(metadata),
            schema=str(data.get("schema") or OBSERVATION_SCHEMA),
        )


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
