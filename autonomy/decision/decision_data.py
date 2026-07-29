"""Immutable DecisionDataSource for shadow action proposals (M006-01)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from autonomy.decision.memory import (
    MemorySnapshot,
    canonical_json_bytes,
    detach_memory_snapshot,
    empty_memory_snapshot,
)
from autonomy.decision.observation import Observation
from autonomy.decision.shadow_ids import (
    deep_freeze,
    frozen_mapping_to_dict,
    require_ascii_id,
    require_code_point_len,
    require_safe_int,
)

DECISION_DATA_SOURCE_SCHEMA = "decision_data_source_v0"
ComponentStatus = Literal["ready", "unavailable", "error"]
COMPONENT_STATUSES = frozenset({"ready", "unavailable", "error"})
MAX_ENVELOPE_REASON = 240
MAX_SOURCE_METADATA_BYTES = 2048


@dataclass(frozen=True)
class ComponentEnvelope:
    """Typed ready/unavailable/error envelope for one decision input."""

    status: str
    value: Any = None
    reason: str = ""
    updated_at_ms: int = 0

    def __post_init__(self) -> None:
        if self.status not in COMPONENT_STATUSES:
            raise ValueError(f"invalid component status {self.status!r}")
        reason = require_code_point_len(
            str(self.reason), field_name="reason", max_len=MAX_ENVELOPE_REASON
        )
        object.__setattr__(self, "reason", reason)
        object.__setattr__(
            self,
            "updated_at_ms",
            require_safe_int(self.updated_at_ms, field_name="updated_at_ms"),
        )
        if self.status == "ready":
            if self.reason != "":
                raise ValueError("ready envelope reason must be empty")
            if self.value is None:
                raise ValueError("ready envelope requires a value")
            # Freeze nested JSON-like payloads; leave typed domain objects as-is
            # after detaching known snapshot/observation types.
            value = self.value
            if isinstance(value, MemorySnapshot):
                value = detach_memory_snapshot(value)
            elif isinstance(value, Observation):
                value = Observation.from_dict(value.to_dict())
            elif isinstance(value, (dict, list, tuple, set)):
                value = deep_freeze(value)
            object.__setattr__(self, "value", value)
        else:
            if self.value is not None:
                raise ValueError(f"{self.status} envelope value must be null")
            if not reason:
                raise ValueError(f"{self.status} envelope requires a non-empty reason")

    def to_dict(self) -> dict[str, Any]:
        value = self.value
        if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
            value = value.to_dict()
        else:
            # Frozen JSON (FrozenJsonObject / nested tuples) → plain dict/list.
            thawed = frozen_mapping_to_dict(value)
            if thawed is not value:
                value = thawed
        return {
            "status": self.status,
            "value": value,
            "reason": self.reason,
            "updated_at_ms": self.updated_at_ms,
        }


def ready_envelope(value: Any, *, updated_at_ms: int = 0) -> ComponentEnvelope:
    return ComponentEnvelope(
        status="ready", value=value, reason="", updated_at_ms=updated_at_ms
    )


def unavailable_envelope(reason: str, *, updated_at_ms: int = 0) -> ComponentEnvelope:
    return ComponentEnvelope(
        status="unavailable",
        value=None,
        reason=reason,
        updated_at_ms=updated_at_ms,
    )


def error_envelope(reason: str, *, updated_at_ms: int = 0) -> ComponentEnvelope:
    return ComponentEnvelope(
        status="error", value=None, reason=reason, updated_at_ms=updated_at_ms
    )


def memory_envelope_from_snapshot(
    snapshot: MemorySnapshot | None,
    *,
    updated_at_ms: int = 0,
) -> ComponentEnvelope:
    """Map MemorySnapshot.health to component envelope (exact proposal table)."""

    if snapshot is None:
        return unavailable_envelope("memory_not_provided", updated_at_ms=updated_at_ms)
    if not isinstance(snapshot, MemorySnapshot):
        raise TypeError("memory envelope requires MemorySnapshot or None")
    detached = detach_memory_snapshot(snapshot)
    health = detached.health
    if health in {"healthy", "empty"}:
        return ready_envelope(detached, updated_at_ms=updated_at_ms or detached.created_at_ms)
    if health == "unavailable":
        reason = detached.error or str(
            (detached.metadata or {}).get("reason") or "memory_unavailable"
        )
        return unavailable_envelope(
            reason,
            updated_at_ms=updated_at_ms or detached.created_at_ms,
        )
    if health == "error":
        return error_envelope(
            f"memory_error:{detached.error or 'unknown'}",
            updated_at_ms=updated_at_ms or detached.created_at_ms,
        )
    raise ValueError(f"unsupported memory health {health!r}")


def observation_envelope_from_value(
    observation: Observation | None,
    *,
    configured: bool = True,
    error: str | None = None,
    updated_at_ms: int = 0,
) -> ComponentEnvelope:
    if error is not None:
        return error_envelope(error, updated_at_ms=updated_at_ms)
    if observation is None:
        if not configured:
            return unavailable_envelope(
                "observation_not_configured", updated_at_ms=updated_at_ms
            )
        return unavailable_envelope("observation_missing", updated_at_ms=updated_at_ms)
    if not isinstance(observation, Observation):
        raise TypeError("observation envelope requires Observation or None")
    return ready_envelope(observation, updated_at_ms=updated_at_ms)


def default_capabilities(
    *,
    max_abs_steering: float = 1.0,
    max_abs_throttle: float = 1.0,
    allows_reverse: bool = True,
    coordinate_frame: str = "image",
) -> dict[str, Any]:
    return {
        "max_abs_steering": float(max_abs_steering),
        "max_abs_throttle": float(max_abs_throttle),
        "allows_reverse": bool(allows_reverse),
        "coordinate_frame": str(coordinate_frame),
    }


@dataclass(frozen=True)
class DecisionDataSource:
    """Immutable cycle-aligned decision inputs for proposal plugins."""

    frame_id: str
    frame_index: int
    timestamp_ms: int
    observation: ComponentEnvelope
    memory: ComponentEnvelope
    patterns: ComponentEnvelope
    projections: ComponentEnvelope
    capabilities: ComponentEnvelope
    prior_host_applied_command: ComponentEnvelope
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = DECISION_DATA_SOURCE_SCHEMA
    source_id: str = ""

    def __post_init__(self) -> None:
        frame_id = require_ascii_id(self.frame_id, field_name="frame_id")
        object.__setattr__(self, "frame_id", frame_id)
        object.__setattr__(
            self, "frame_index", require_safe_int(self.frame_index, field_name="frame_index")
        )
        object.__setattr__(
            self,
            "timestamp_ms",
            require_safe_int(self.timestamp_ms, field_name="timestamp_ms"),
        )
        source_id = self.source_id or f"decision-data:{frame_id}"
        if source_id != f"decision-data:{frame_id}":
            raise ValueError("source_id must be decision-data:{frame_id}")
        object.__setattr__(self, "source_id", source_id)
        if self.schema != DECISION_DATA_SOURCE_SCHEMA:
            raise ValueError(
                f"schema must be {DECISION_DATA_SOURCE_SCHEMA!r}; got {self.schema!r}"
            )
        for name in (
            "observation",
            "memory",
            "patterns",
            "projections",
            "capabilities",
            "prior_host_applied_command",
        ):
            envelope = getattr(self, name)
            if not isinstance(envelope, ComponentEnvelope):
                raise TypeError(f"{name} must be ComponentEnvelope")
        if type(self.metadata) is not dict:
            raise TypeError("metadata must be a dict (JSON object)")
        metadata = deep_freeze(self.metadata)
        meta_bytes = canonical_json_bytes(frozen_mapping_to_dict(metadata))
        if meta_bytes > MAX_SOURCE_METADATA_BYTES:
            raise ValueError(
                f"DecisionDataSource metadata exceeds {MAX_SOURCE_METADATA_BYTES} bytes"
            )
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "observation": self.observation.to_dict(),
            "memory": self.memory.to_dict(),
            "patterns": self.patterns.to_dict(),
            "projections": self.projections.to_dict(),
            "capabilities": self.capabilities.to_dict(),
            "prior_host_applied_command": self.prior_host_applied_command.to_dict(),
            "metadata": frozen_mapping_to_dict(self.metadata),
        }


def build_decision_data_source(
    *,
    frame_id: str,
    frame_index: int,
    timestamp_ms: int,
    observation: Observation | None = None,
    observation_configured: bool = True,
    observation_error: str | None = None,
    memory: MemorySnapshot | None = None,
    patterns: ComponentEnvelope | None = None,
    projections: ComponentEnvelope | None = None,
    capabilities: ComponentEnvelope | None = None,
    prior_host_applied_command: ComponentEnvelope | None = None,
    metadata: dict[str, Any] | None = None,
) -> DecisionDataSource:
    """Build a frozen DecisionDataSource for one cycle."""

    return DecisionDataSource(
        frame_id=frame_id,
        frame_index=frame_index,
        timestamp_ms=timestamp_ms,
        observation=observation_envelope_from_value(
            observation,
            configured=observation_configured,
            error=observation_error,
            updated_at_ms=timestamp_ms,
        ),
        memory=memory_envelope_from_snapshot(memory, updated_at_ms=timestamp_ms),
        patterns=patterns
        or unavailable_envelope("stage_not_configured", updated_at_ms=timestamp_ms),
        projections=projections
        or unavailable_envelope("stage_not_configured", updated_at_ms=timestamp_ms),
        capabilities=capabilities
        or ready_envelope(default_capabilities(), updated_at_ms=timestamp_ms),
        prior_host_applied_command=prior_host_applied_command
        or unavailable_envelope(
            "host_did_not_report_applied_command", updated_at_ms=timestamp_ms
        ),
        metadata={} if metadata is None else metadata,
    )


# Re-export for tests that need empty snapshots without importing memory helpers deeply.
__all__ = [
    "COMPONENT_STATUSES",
    "ComponentEnvelope",
    "DECISION_DATA_SOURCE_SCHEMA",
    "DecisionDataSource",
    "build_decision_data_source",
    "default_capabilities",
    "empty_memory_snapshot",
    "error_envelope",
    "memory_envelope_from_snapshot",
    "observation_envelope_from_value",
    "ready_envelope",
    "unavailable_envelope",
]
