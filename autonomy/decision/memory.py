"""Stable memory values for retained decision evidence.

Memory records what attributed observation evidence remains relevant across
cycles. It is not a world model, semantic identity layer, or action policy.
Concrete reducers live under implementations/; this module owns only the
inspectable value contract and lifecycle fields.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from autonomy.perception import ViewLocation


MEMORY_SNAPSHOT_SCHEMA = "decision_memory_snapshot_v0"
# Visible defaults for activation and implementations that omit size ceilings.
DEFAULT_MAX_PROPERTY_BYTES = 4_096
DEFAULT_MAX_SERIALIZED_BYTES = 262_144

MemoryHealth = Literal["empty", "healthy", "unavailable", "error"]
MEMORY_HEALTH_VALUES: frozenset[str] = frozenset(
    ("empty", "healthy", "unavailable", "error")
)


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
    """One bounded retained claim derived from prior observation evidence."""

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


@dataclass(frozen=True)
class MemoryBounds:
    """Finite capacity, age, and size policy visible on every snapshot."""

    max_records: int
    max_age_ms: int | None = None
    eviction_policy: str = "oldest_first"
    max_property_bytes: int | None = DEFAULT_MAX_PROPERTY_BYTES
    max_serialized_bytes: int | None = DEFAULT_MAX_SERIALIZED_BYTES

    def __post_init__(self) -> None:
        max_records = _require_positive_int(self.max_records, field_name="max_records")
        object.__setattr__(self, "max_records", max_records)
        if self.max_age_ms is not None:
            object.__setattr__(
                self,
                "max_age_ms",
                _require_positive_int(self.max_age_ms, field_name="max_age_ms"),
            )
        _require_identifier(self.eviction_policy, field_name="eviction_policy")
        if self.max_property_bytes is not None:
            object.__setattr__(
                self,
                "max_property_bytes",
                _require_positive_int(
                    self.max_property_bytes, field_name="max_property_bytes"
                ),
            )
        if self.max_serialized_bytes is not None:
            object.__setattr__(
                self,
                "max_serialized_bytes",
                _require_positive_int(
                    self.max_serialized_bytes, field_name="max_serialized_bytes"
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryBounds":
        max_age = data.get("max_age_ms")
        max_property = data.get("max_property_bytes", DEFAULT_MAX_PROPERTY_BYTES)
        max_serialized = data.get("max_serialized_bytes", DEFAULT_MAX_SERIALIZED_BYTES)
        return cls(
            max_records=int(data.get("max_records") or 0),
            max_age_ms=int(max_age) if max_age is not None else None,
            eviction_policy=str(data.get("eviction_policy") or "oldest_first"),
            max_property_bytes=(
                int(max_property) if max_property is not None else None
            ),
            max_serialized_bytes=(
                int(max_serialized) if max_serialized is not None else None
            ),
        )


@dataclass(frozen=True)
class MemorySnapshot:
    """Detached, inspectable memory state after one cycle update or reset."""

    memory_id: str
    epoch_id: str
    health: str
    bounds: MemoryBounds
    created_at_ms: int
    records: tuple[RetainedEvidence, ...] = ()
    summary: tuple[str, ...] = ()
    implementation_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = MEMORY_SNAPSHOT_SCHEMA

    def __post_init__(self) -> None:
        _require_identifier(self.memory_id, field_name="memory_id")
        _require_identifier(self.epoch_id, field_name="epoch_id")
        if self.health not in MEMORY_HEALTH_VALUES:
            raise ValueError(
                f"memory health must be one of {sorted(MEMORY_HEALTH_VALUES)}; "
                f"got {self.health!r}"
            )
        if not isinstance(self.bounds, MemoryBounds):
            raise TypeError("memory bounds must be MemoryBounds")
        object.__setattr__(
            self,
            "created_at_ms",
            _require_non_negative_int(self.created_at_ms, field_name="created_at_ms"),
        )
        records = tuple(self.records)
        if len(records) > self.bounds.max_records:
            raise ValueError(
                f"memory snapshot has {len(records)} records but max_records="
                f"{self.bounds.max_records}"
            )
        record_ids = [record.record_id for record in records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("memory snapshot cannot repeat record ids")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "summary", tuple(str(item) for item in self.summary))
        if self.implementation_id is not None:
            _require_identifier(
                self.implementation_id, field_name="implementation_id"
            )
        if self.health == "error" and not (self.error and str(self.error).strip()):
            raise ValueError("error memory snapshots require a non-empty error")
        if self.health == "empty" and records:
            raise ValueError("empty memory snapshots cannot retain records")
        if self.health == "healthy" and not records:
            raise ValueError("healthy memory snapshots require at least one record")
        object.__setattr__(self, "metadata", deepcopy(dict(self.metadata)))

    @property
    def record_count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "memory_id": self.memory_id,
            "epoch_id": self.epoch_id,
            "health": self.health,
            "bounds": self.bounds.to_dict(),
            "created_at_ms": self.created_at_ms,
            "record_count": self.record_count,
            "records": [record.to_dict() for record in self.records],
            "summary": list(self.summary),
            "implementation_id": self.implementation_id,
            "error": self.error,
            "metadata": deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemorySnapshot":
        bounds_data = data.get("bounds")
        if not isinstance(bounds_data, dict):
            raise ValueError("memory snapshot requires bounds")
        records_data = data.get("records") or ()
        records = tuple(
            RetainedEvidence.from_dict(item)
            for item in records_data
            if isinstance(item, dict)
        )
        return cls(
            memory_id=str(data.get("memory_id") or ""),
            epoch_id=str(data.get("epoch_id") or ""),
            health=str(data.get("health") or "unavailable"),
            bounds=MemoryBounds.from_dict(bounds_data),
            created_at_ms=int(data.get("created_at_ms") or 0),
            records=records,
            summary=tuple(str(item) for item in (data.get("summary") or ())),
            implementation_id=(
                str(data["implementation_id"])
                if data.get("implementation_id") is not None
                else None
            ),
            error=(str(data["error"]) if data.get("error") is not None else None),
            metadata=deepcopy(dict(data.get("metadata") or {})),
            schema=str(data.get("schema") or MEMORY_SNAPSHOT_SCHEMA),
        )


def detach_memory_snapshot(snapshot: "MemorySnapshot") -> "MemorySnapshot":
    """Return a deep copy so callers cannot mutate implementation-owned state."""

    if not isinstance(snapshot, MemorySnapshot):
        raise TypeError(
            f"detach_memory_snapshot requires MemorySnapshot; got {type(snapshot).__name__}"
        )
    return MemorySnapshot.from_dict(snapshot.to_dict())


def canonical_json_bytes(value: Any) -> int:
    """UTF-8 byte length of strict canonical JSON.

    Rejects non-JSON types and non-finite numbers. Callers must not retain values
    that cannot be measured with this path.
    """

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"value is not strictly JSON-serializable: {type(exc).__name__}: {exc}"
        ) from exc
    return len(encoded)


def ensure_strict_json_value(value: Any) -> Any:
    """Round-trip through strict JSON or raise ValueError."""

    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"value is not strictly JSON-serializable: {type(exc).__name__}: {exc}"
        ) from exc


def serialized_memory_snapshot_bytes(snapshot: "MemorySnapshot") -> int:
    """UTF-8 byte length of the compact strict JSON form of a snapshot."""

    return canonical_json_bytes(snapshot.to_dict())


def serialized_mapping_bytes(value: Any) -> int:
    """UTF-8 byte length of a compact strict JSON mapping (for property bags)."""

    return canonical_json_bytes(value)


def empty_memory_snapshot(
    *,
    memory_id: str,
    epoch_id: str,
    bounds: MemoryBounds,
    created_at_ms: int,
    implementation_id: str | None = None,
    summary: tuple[str, ...] = ("memory_empty=true",),
    metadata: dict[str, Any] | None = None,
) -> MemorySnapshot:
    """Construct an explicit empty snapshot after reset or with no retained claims."""

    return MemorySnapshot(
        memory_id=memory_id,
        epoch_id=epoch_id,
        health="empty",
        bounds=bounds,
        created_at_ms=created_at_ms,
        records=(),
        summary=summary,
        implementation_id=implementation_id,
        metadata=metadata or {},
    )


def unavailable_memory_snapshot(
    *,
    memory_id: str,
    epoch_id: str,
    bounds: MemoryBounds,
    created_at_ms: int,
    reason: str,
    implementation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> MemorySnapshot:
    """Construct a snapshot that reports memory as unavailable without retained claims."""

    return MemorySnapshot(
        memory_id=memory_id,
        epoch_id=epoch_id,
        health="unavailable",
        bounds=bounds,
        created_at_ms=created_at_ms,
        records=(),
        summary=(f"memory_available=false reason={reason}",),
        implementation_id=implementation_id,
        metadata=metadata or {"reason": reason},
    )


def error_memory_snapshot(
    *,
    memory_id: str,
    epoch_id: str,
    bounds: MemoryBounds,
    created_at_ms: int,
    error: str,
    implementation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> MemorySnapshot:
    """Construct an explicit failure snapshot that retains no new claims."""

    return MemorySnapshot(
        memory_id=memory_id,
        epoch_id=epoch_id,
        health="error",
        bounds=bounds,
        created_at_ms=created_at_ms,
        records=(),
        summary=(f"memory_error={error}",),
        implementation_id=implementation_id,
        error=error,
        metadata=metadata or {},
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


def _require_positive_int(value: Any, *, field_name: str) -> int:
    normalized = _require_non_negative_int(value, field_name=field_name)
    if normalized <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return normalized


def _normalized_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be numeric") from exc
    if not math.isfinite(confidence):
        raise ValueError("confidence must be finite")
    return max(0.0, min(1.0, confidence))
