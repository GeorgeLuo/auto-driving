"""Evidence records that memory plugins retain and proposal plugins read.

``RetainedEvidence`` is one attributed record derived from an observation;
``MemoryProvenance`` says where it came from. Plugins choose where in
``shared_memory`` to publish records; these types only fix their shape.
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.decision_cycle.perception.evidence.values import ViewLocation


@dataclass(frozen=True)
class MemoryProvenance:
    """Attribution for one retained evidence record."""

    observation_id: str
    evidence_id: str
    coordinate_frame: str
    observed_at_ms: int
    updated_at_ms: int
    source_plugin_id: str | None = None
    frame_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.observation_id, field_name="observation_id")
        _require_identifier(self.evidence_id, field_name="evidence_id")
        _require_identifier(self.coordinate_frame, field_name="coordinate_frame")
        object.__setattr__(
            self,
            "observed_at_ms",
            _require_non_negative_int(self.observed_at_ms, field_name="observed_at_ms"),
        )
        object.__setattr__(
            self,
            "updated_at_ms",
            _require_non_negative_int(self.updated_at_ms, field_name="updated_at_ms"),
        )
        if self.source_plugin_id is not None:
            _require_identifier(self.source_plugin_id, field_name="source_plugin_id")
        if self.frame_id is not None:
            _require_identifier(self.frame_id, field_name="frame_id")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryProvenance":
        return cls(
            observation_id=str(data.get("observation_id") or ""),
            evidence_id=str(data.get("evidence_id") or ""),
            coordinate_frame=str(data.get("coordinate_frame") or "unknown"),
            observed_at_ms=int(data.get("observed_at_ms") or 0),
            updated_at_ms=int(data.get("updated_at_ms") or 0),
            source_plugin_id=(
                str(data["source_plugin_id"])
                if data.get("source_plugin_id") is not None
                else None
            ),
            frame_id=(
                str(data["frame_id"]) if data.get("frame_id") is not None else None
            ),
        )


@dataclass(frozen=True)
class RetainedEvidence:
    """One retained-evidence record derived from an observation."""

    record_id: str
    kind: str
    label: str
    confidence: float
    provenance: MemoryProvenance
    location: ViewLocation | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.record_id, field_name="record_id")
        _require_identifier(self.kind, field_name="kind")
        _require_identifier(self.label, field_name="label")
        object.__setattr__(
            self,
            "confidence",
            _normalized_confidence(self.confidence),
        )
        if not isinstance(self.provenance, MemoryProvenance):
            raise TypeError("retained evidence provenance must be MemoryProvenance")
        if self.location is not None and not isinstance(self.location, ViewLocation):
            raise TypeError("retained evidence location must be ViewLocation or None")
        object.__setattr__(self, "properties", deepcopy(dict(self.properties)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "label": self.label,
            "confidence": self.confidence,
            "provenance": self.provenance.to_dict(),
            "location": self.location.to_dict() if self.location is not None else None,
            "properties": deepcopy(self.properties),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetainedEvidence":
        provenance_data = data.get("provenance")
        if not isinstance(provenance_data, dict):
            raise ValueError("retained evidence requires provenance")
        location_data = data.get("location")
        location = (
            ViewLocation.from_dict(location_data)
            if isinstance(location_data, dict)
            else None
        )
        return cls(
            record_id=str(data.get("record_id") or ""),
            kind=str(data.get("kind") or ""),
            label=str(data.get("label") or ""),
            confidence=float(data.get("confidence") or 0.0),
            provenance=MemoryProvenance.from_dict(provenance_data),
            location=location,
            properties=deepcopy(dict(data.get("properties") or {})),
        )



def _require_identifier(value: Any, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if normalized < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return normalized


def _normalized_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be numeric") from exc
    if not math.isfinite(confidence):
        raise ValueError("confidence must be finite")
    return max(0.0, min(1.0, confidence))


def detach_evidence(records: Any) -> tuple[RetainedEvidence, ...]:
    """Return detached copies so readers cannot mutate a producer's records."""

    return tuple(RetainedEvidence.from_dict(record.to_dict()) for record in records)
