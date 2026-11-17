"""Evidence records that memory plugins retain and proposal plugins read.

``RetainedEvidence`` is one attributed record derived from an observation;
``MemoryOrigin`` says where it came from. Plugins choose where in
``shared_memory`` to publish records; these types only fix their shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.decision_cycle.confidence import optional_confidence
from autonomy.fields import (
    FieldError, require_ascii_id as require_identifier, require_keys, require_nested,
    require_number, require_safe_int as require_non_negative_int, require_text,
)
from autonomy.decision_cycle.perception.evidence.values import ViewLocation


@dataclass(frozen=True)
class MemoryOrigin:
    """Attribution for one retained evidence record.

    ``observation_id`` is the observation the record came from; ``observed_id``
    is the id of the thing or signal in it that the record was derived from.
    ``updated_at_ms`` and ``frame_id`` are when, and in which decision frame,
    memory last wrote the record.
    """

    observation_id: str
    observed_id: str
    coordinate_frame: str
    observed_at_ms: int
    updated_at_ms: int
    source_plugin_id: str | None = None
    frame_id: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.observation_id, field_name="observation_id")
        require_identifier(self.observed_id, field_name="observed_id")
        require_identifier(self.coordinate_frame, field_name="coordinate_frame")
        object.__setattr__(
            self,
            "observed_at_ms",
            require_non_negative_int(self.observed_at_ms, field_name="observed_at_ms"),
        )
        object.__setattr__(
            self,
            "updated_at_ms",
            require_non_negative_int(self.updated_at_ms, field_name="updated_at_ms"),
        )
        if self.source_plugin_id is not None:
            require_identifier(self.source_plugin_id, field_name="source_plugin_id")
        if self.frame_id is not None:
            require_identifier(self.frame_id, field_name="frame_id")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryOrigin":
        document = require_keys(data, (
            "observation_id", "observed_id", "coordinate_frame", "observed_at_ms",
            "updated_at_ms", "source_plugin_id", "frame_id",
        ))
        return cls(**document)


@dataclass(frozen=True)
class RetainedEvidence:
    """One retained-evidence record derived from an observation."""

    record_id: str
    kind: str
    label: str
    origin: MemoryOrigin
    confidence: float | None = None
    location: ViewLocation | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_identifier(self.record_id, field_name="record_id")
        require_identifier(self.kind, field_name="kind")
        require_text(self.label, field_name="label")
        object.__setattr__(
            self,
            "confidence",
            optional_confidence(self.confidence, field_name="confidence", clamp=True),
        )
        if not isinstance(self.origin, MemoryOrigin):
            raise FieldError("origin", "must be MemoryOrigin")
        if self.location is not None and not isinstance(self.location, ViewLocation):
            raise FieldError("location", "must be ViewLocation or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "label": self.label,
            "confidence": self.confidence,
            "origin": self.origin.to_dict(),
            "location": self.location.to_dict() if self.location is not None else None,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetainedEvidence":
        document = require_keys(data, ("record_id", "kind", "label", "origin", "confidence", "location", "properties"))
        if not isinstance(document["properties"], dict):
            raise FieldError("properties", "must be an object")
        confidence = document["confidence"]
        if confidence is not None:
            confidence = require_number(confidence, field_name="confidence", bounds=(0, 1))
        location = document["location"]
        if location is not None and (not isinstance(location, dict) or not location):
            raise FieldError("location", "must be a nonempty object or null")
        return cls(
            record_id=document["record_id"], kind=document["kind"], label=document["label"],
            confidence=confidence,
            origin=require_nested(MemoryOrigin.from_dict, document["origin"], field_name="origin"),
            location=require_nested(ViewLocation.from_dict, location, field_name="location") if location is not None else None,
            properties=document["properties"],
        )
