"""The evidence ledger value and its bounds.

``EvidenceLedger`` is a memory plugin's state: the retained records plus the
epoch, bounds, and bookkeeping that later frames need. Each plugin keeps it at
its own shared-memory key and publishes the records alone at ``EVIDENCE_KEY``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.decision_cycle.memory.evidence import RetainedEvidence
from autonomy.decision_cycle.memory.interface import LEDGER_HEALTH_VALUES
from autonomy.serialization import canonical_json_size_bytes
from autonomy.fields import (
    FieldError, require_ascii_id, require_choice, require_keys, require_nested,
    require_safe_int, require_schema, require_text,
)

LEDGER_SCHEMA = "bounded_evidence_ledger"

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
        require_choice(self.eviction_policy, (DEFAULT_EVICTION_POLICY,), field_name="eviction_policy")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LedgerBounds":
        return cls(**require_keys(data, (
            "max_records", "max_age_ms", "eviction_policy", "max_property_bytes", "max_serialized_bytes",
        )))


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
            require_ascii_id(getattr(self, name), field_name=name)
        if self.plugin_id is not None:
            require_ascii_id(self.plugin_id, field_name="plugin_id")
        require_choice(self.health, LEDGER_HEALTH_VALUES, field_name="health")
        if not isinstance(self.bounds, LedgerBounds):
            raise FieldError("bounds", "must be LedgerBounds")
        require_safe_int(self.created_at_ms, field_name="created_at_ms")
        records = tuple(self.records)
        if len(records) > self.bounds.max_records:
            raise FieldError("records",
                f"ledger has {len(records)} records but max_records={self.bounds.max_records}"
            )
        record_ids = [record.record_id for record in records]
        if len(record_ids) != len(set(record_ids)):
            raise FieldError("records", "cannot repeat record ids")
        if self.health == "empty" and records:
            raise FieldError("health", "an empty ledger cannot retain records")
        if self.health == "healthy" and not records:
            raise FieldError("health", "a healthy ledger requires at least one record")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "summary", tuple(self.summary))

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
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceLedger":
        document = require_schema(data, LEDGER_SCHEMA, (
            "schema", "memory_id", "epoch_id", "health", "bounds", "created_at_ms",
            "record_count", "records", "summary", "plugin_id", "metadata",
        ))
        records = document["records"]
        if not isinstance(records, (list, tuple)):
            raise FieldError("records", "must be an array")
        summary = document["summary"]
        if not isinstance(summary, (list, tuple)):
            raise FieldError("summary", "must be an array")
        for index, item in enumerate(summary):
            require_text(item, field_name=f"summary.{index}")
        if not isinstance(document["metadata"], dict):
            raise FieldError("metadata", "must be an object")
        ledger = cls(
            memory_id=document["memory_id"], epoch_id=document["epoch_id"], health=document["health"],
            bounds=require_nested(LedgerBounds.from_dict, document["bounds"], field_name="bounds"),
            created_at_ms=document["created_at_ms"],
            records=tuple(require_nested(RetainedEvidence.from_dict, item, field_name=f"records.{index}")
                          for index, item in enumerate(records)),
            summary=document["summary"], plugin_id=document["plugin_id"],
            metadata=document["metadata"], schema=document["schema"],
        )
        if require_safe_int(document["record_count"], field_name="record_count") != ledger.record_count:
            raise FieldError("record_count", "must agree with records")
        return ledger


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


def serialized_ledger_bytes(ledger: EvidenceLedger) -> int:
    """UTF-8 byte length of the compact strict JSON form of a ledger."""

    return canonical_json_size_bytes(ledger.to_dict())


def _require_positive_int(value: Any, *, field_name: str) -> int:
    normalized = require_safe_int(value, field_name=field_name)
    if normalized == 0:
        raise FieldError(field_name, "must be > 0")
    return normalized
