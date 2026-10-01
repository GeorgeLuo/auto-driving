"""The bounded-evidence ledger value, its bounds, and its shared-memory keys.

``EvidenceLedger`` is the plugin's state: the retained records plus the epoch,
bounds, and bookkeeping that later frames need. The plugin keeps it at
``LEDGER_KEY``. ``EVIDENCE_KEY`` carries the retained records alone for other
plugins; ``avoid_recent_obstruction`` reads it by default, and another memory
plugin can publish its own records there instead.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.decision_cycle.memory.evidence import RetainedEvidence
from autonomy.serialization import canonical_json_size_bytes

LEDGER_SCHEMA = "bounded_evidence_ledger_v0"
LEDGER_KEY = "bounded_evidence.ledger"
EVIDENCE_KEY = "retained_evidence"
LEDGER_HEALTH_VALUES: frozenset[str] = frozenset(("empty", "healthy"))

DEFAULT_MAX_RECORDS = 32
DEFAULT_MAX_AGE_MS = 10_000
DEFAULT_EVICTION_POLICY = "oldest_first"
DEFAULT_MAX_PROPERTY_BYTES = 4_096
DEFAULT_MAX_SERIALIZED_BYTES = 262_144


@dataclass(frozen=True)
class LedgerBounds:
    """Finite capacity, age, and size policy for the ledger."""

    max_records: int
    max_age_ms: int | None = None
    eviction_policy: str = DEFAULT_EVICTION_POLICY
    max_property_bytes: int | None = DEFAULT_MAX_PROPERTY_BYTES
    max_serialized_bytes: int | None = DEFAULT_MAX_SERIALIZED_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "max_records", _require_positive_int(self.max_records, field_name="max_records")
        )
        for name in ("max_age_ms", "max_property_bytes", "max_serialized_bytes"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_positive_int(value, field_name=name))
        if not isinstance(self.eviction_policy, str) or not self.eviction_policy.strip():
            raise ValueError("eviction_policy must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LedgerBounds":
        max_age = data.get("max_age_ms")
        max_property = data.get("max_property_bytes", DEFAULT_MAX_PROPERTY_BYTES)
        max_serialized = data.get("max_serialized_bytes", DEFAULT_MAX_SERIALIZED_BYTES)
        return cls(
            max_records=int(data.get("max_records") or 0),
            max_age_ms=int(max_age) if max_age is not None else None,
            eviction_policy=str(data.get("eviction_policy") or DEFAULT_EVICTION_POLICY),
            max_property_bytes=int(max_property) if max_property is not None else None,
            max_serialized_bytes=int(max_serialized) if max_serialized is not None else None,
        )


def bounds_from_config(config: dict[str, Any]) -> LedgerBounds:
    max_age_ms = config.get("max_age_ms", DEFAULT_MAX_AGE_MS)
    max_property_bytes = config.get("max_property_bytes", DEFAULT_MAX_PROPERTY_BYTES)
    max_serialized_bytes = config.get("max_serialized_bytes", DEFAULT_MAX_SERIALIZED_BYTES)
    return LedgerBounds(
        max_records=int(config.get("max_records", DEFAULT_MAX_RECORDS)),
        max_age_ms=int(max_age_ms) if max_age_ms is not None else None,
        eviction_policy=str(config.get("eviction_policy") or DEFAULT_EVICTION_POLICY),
        max_property_bytes=int(max_property_bytes) if max_property_bytes is not None else None,
        max_serialized_bytes=(
            int(max_serialized_bytes) if max_serialized_bytes is not None else None
        ),
    )


@dataclass(frozen=True)
class EvidenceLedger:
    """Retained records and the bookkeeping that later frames read back."""

    memory_id: str
    epoch_id: str
    health: str
    bounds: LedgerBounds
    created_at_ms: int
    records: tuple[RetainedEvidence, ...] = ()
    summary: tuple[str, ...] = ()
    plugin_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = LEDGER_SCHEMA

    def __post_init__(self) -> None:
        for name in ("memory_id", "epoch_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.health not in LEDGER_HEALTH_VALUES:
            raise ValueError(
                f"ledger health must be one of {sorted(LEDGER_HEALTH_VALUES)}; got {self.health!r}"
            )
        if not isinstance(self.bounds, LedgerBounds):
            raise TypeError("ledger bounds must be LedgerBounds")
        object.__setattr__(
            self,
            "created_at_ms",
            _require_non_negative_int(self.created_at_ms, field_name="created_at_ms"),
        )
        records = tuple(self.records)
        if len(records) > self.bounds.max_records:
            raise ValueError(
                f"ledger has {len(records)} records but max_records={self.bounds.max_records}"
            )
        record_ids = [record.record_id for record in records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("ledger cannot repeat record ids")
        if self.health == "empty" and records:
            raise ValueError("an empty ledger cannot retain records")
        if self.health == "healthy" and not records:
            raise ValueError("a healthy ledger requires at least one record")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "summary", tuple(str(item) for item in self.summary))
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
            "plugin_id": self.plugin_id,
            "metadata": deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceLedger":
        bounds_data = data.get("bounds")
        if not isinstance(bounds_data, dict):
            raise ValueError("ledger requires bounds")
        return cls(
            memory_id=str(data.get("memory_id") or ""),
            epoch_id=str(data.get("epoch_id") or ""),
            health=str(data.get("health") or "empty"),
            bounds=LedgerBounds.from_dict(bounds_data),
            created_at_ms=int(data.get("created_at_ms") or 0),
            records=tuple(
                RetainedEvidence.from_dict(item)
                for item in (data.get("records") or ())
                if isinstance(item, dict)
            ),
            summary=tuple(str(item) for item in (data.get("summary") or ())),
            plugin_id=(
                str(data["plugin_id"]) if data.get("plugin_id") is not None else None
            ),
            metadata=deepcopy(dict(data.get("metadata") or {})),
            schema=str(data.get("schema") or LEDGER_SCHEMA),
        )


def empty_ledger(
    *,
    memory_id: str,
    epoch_id: str,
    bounds: LedgerBounds,
    created_at_ms: int,
    plugin_id: str | None = None,
    summary: tuple[str, ...] = ("memory_empty=true",),
    metadata: dict[str, Any] | None = None,
) -> EvidenceLedger:
    """An empty ledger after reset or with no retained claims."""

    return EvidenceLedger(
        memory_id=memory_id,
        epoch_id=epoch_id,
        health="empty",
        bounds=bounds,
        created_at_ms=created_at_ms,
        records=(),
        summary=summary,
        plugin_id=plugin_id,
        metadata=metadata or {},
    )


def detach_ledger(ledger: EvidenceLedger) -> EvidenceLedger:
    """Return a deep copy so readers cannot mutate the stored ledger."""

    if not isinstance(ledger, EvidenceLedger):
        raise TypeError(f"detach_ledger requires EvidenceLedger; got {type(ledger).__name__}")
    return EvidenceLedger.from_dict(ledger.to_dict())


def serialized_ledger_bytes(ledger: EvidenceLedger) -> int:
    """UTF-8 byte length of the compact strict JSON form of a ledger."""

    return canonical_json_size_bytes(ledger.to_dict())


def serialized_mapping_bytes(value: Any) -> int:
    """UTF-8 byte length of a compact strict JSON mapping (for property bags)."""

    return canonical_json_size_bytes(value)


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
