"""Current-frame observation record."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping

from autonomy.serialization import deep_freeze, frozen_mapping_to_dict
from typing import Any


from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, PerceptionSignal
from autonomy.fields import FieldError, require_ascii_id, require_nested, require_safe_int, require_schema, require_text


OBSERVATION_SCHEMA = "decision_observation"


@dataclass(frozen=True)
class Observation:
    """Current-frame record assembled from perception evidence and sensor context.

    This is the observation step's output.
    """

    observation_id: str
    created_at_ms: int
    sensor_frame: Mapping[str, object]
    perception_schema: str | None = None
    perception_plugin_id: str | None = None
    summary: tuple[str, ...] = ()
    things: tuple[PerceivedThing, ...] = ()
    signals: tuple[PerceptionSignal, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema: str = OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        require_ascii_id(self.observation_id, field_name="observation_id")
        require_safe_int(self.created_at_ms, field_name="created_at_ms")
        for name in ("perception_schema", "perception_plugin_id"):
            value = getattr(self, name)
            if value is not None:
                require_ascii_id(value, field_name=name)
        for name, item_type in (("things", PerceivedThing), ("signals", PerceptionSignal)):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)):
                raise FieldError(name, "must be an array")
            for index, value in enumerate(values):
                if not isinstance(value, item_type):
                    raise FieldError(f"{name}.{index}", f"must be {item_type.__name__}")
            object.__setattr__(self, name, tuple(values))
        object.__setattr__(self, "summary", tuple(self.summary))

        for name in ("sensor_frame", "metadata"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise FieldError(name, "must be an object")
            try:
                object.__setattr__(self, name, deep_freeze(value, field_name=name))
            except ValueError as exc:
                raise FieldError(name, str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "observation_id": self.observation_id,
            "created_at_ms": self.created_at_ms, "sensor_frame": frozen_mapping_to_dict(self.sensor_frame),
            "perception_schema": self.perception_schema, "perception_plugin_id": self.perception_plugin_id,
            "summary": self.summary, "things": tuple(thing.to_dict() for thing in self.things),
            "signals": tuple(signal.to_dict() for signal in self.signals), "metadata": frozen_mapping_to_dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Observation":
        document = require_schema(data, OBSERVATION_SCHEMA, (
            "schema", "observation_id", "created_at_ms", "sensor_frame", "perception_schema",
            "perception_plugin_id", "summary", "things", "signals", "metadata",
        ))
        for name in ("sensor_frame", "metadata"):
            if not isinstance(document[name], dict):
                raise FieldError(name, "must be an object")
        summary = document["summary"]
        if not isinstance(summary, (list, tuple)):
            raise FieldError("summary", "must be an array")
        for index, value in enumerate(summary):
            require_text(value, field_name=f"summary.{index}")
        parsed = {}
        for name, parse in (("things", PerceivedThing.from_dict), ("signals", PerceptionSignal.from_dict)):
            values = document[name]
            if not isinstance(values, (list, tuple)):
                raise FieldError(name, "must be an array")
            parsed[name] = tuple(require_nested(parse, value, field_name=f"{name}.{index}")
                                 for index, value in enumerate(values))
        return cls(
            schema=document["schema"], observation_id=document["observation_id"],
            created_at_ms=document["created_at_ms"], sensor_frame=document["sensor_frame"],
            perception_schema=document["perception_schema"], perception_plugin_id=document["perception_plugin_id"],
            summary=document["summary"], metadata=document["metadata"], **parsed,
        )
