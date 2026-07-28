"""ActionProposal and ProposedVehicleCommand contracts (M006-02)."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from autonomy.decision.memory import canonical_json_bytes
from autonomy.decision.shadow_ids import (
    proposal_id_for,
    require_ascii_id,
    require_code_point_len,
)
from autonomy.vehicle import clamp_unit

ACTION_PROPOSAL_SCHEMA = "action_proposal_v0"
PROPOSED_VEHICLE_COMMAND_SCHEMA = "proposed_vehicle_command_v0"
MAX_REASON = 240
MAX_ASSUMPTION = 64
MAX_ASSUMPTIONS = 8
MAX_SOURCE_REFS = 16
MAX_SOURCE_REF_ID = 128
MAX_SOURCE_REF_NOTE = 64
MAX_PROPOSAL_METADATA_BYTES = 1024
MAX_PROPOSAL_BYTES = 4096
COMMAND_EPS = 1e-9

Lifecycle = Literal[
    "fresh",
    "retained",
    "stale",
    "inactive",
    "incompatible",
    "missing_input",
    "error",
]
Freshness = Literal["fresh", "retained", "stale", "none"]
LIFECYCLES = frozenset(
    {
        "fresh",
        "retained",
        "stale",
        "inactive",
        "incompatible",
        "missing_input",
        "error",
    }
)
FRESHNESS_VALUES = frozenset({"fresh", "retained", "stale", "none"})

# lifecycle -> allowed freshness, available, command required?, min source_refs
_MATRIX: dict[str, tuple[str, bool, bool, int]] = {
    "fresh": ("fresh", True, True, 1),
    "retained": ("retained", True, True, 1),
    "stale": ("stale", False, False, 1),
    "inactive": ("none", False, False, 0),
    "incompatible": ("none", False, False, 0),
    "missing_input": ("none", False, False, 0),
    "error": ("none", False, False, 0),
}


@dataclass(frozen=True)
class ProposedVehicleCommand:
    """Canonical proposed vehicle command (not applied AutonomyControl)."""

    steering: float
    throttle: float
    gear: str = "hold"
    normalized: bool = True
    schema: str = PROPOSED_VEHICLE_COMMAND_SCHEMA

    def __post_init__(self) -> None:
        try:
            steering = float(self.steering)
            throttle = float(self.throttle)
        except (TypeError, ValueError) as exc:
            raise ValueError("steering and throttle must be numeric") from exc
        if not math.isfinite(steering) or not math.isfinite(throttle):
            raise ValueError("steering and throttle must be finite")
        steering = clamp_unit(steering)
        throttle = clamp_unit(throttle)
        if abs(throttle) < COMMAND_EPS:
            gear = "hold"
        elif throttle > 0:
            gear = "forward"
        else:
            gear = "reverse"
        if self.gear not in {"forward", "reverse", "hold"}:
            raise ValueError(f"invalid gear {self.gear!r}")
        if self.gear != gear:
            raise ValueError(f"gear {self.gear!r} contradicts throttle {throttle}")
        object.__setattr__(self, "steering", steering)
        object.__setattr__(self, "throttle", throttle)
        object.__setattr__(self, "gear", gear)
        object.__setattr__(self, "normalized", True)
        if self.schema != PROPOSED_VEHICLE_COMMAND_SCHEMA:
            raise ValueError("invalid ProposedVehicleCommand schema")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "steering": self.steering,
            "throttle": self.throttle,
            "gear": self.gear,
            "normalized": True,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProposedVehicleCommand":
        return cls(
            steering=float(data.get("steering") or 0.0),
            throttle=float(data.get("throttle") or 0.0),
            gear=str(data.get("gear") or "hold"),
            schema=str(data.get("schema") or PROPOSED_VEHICLE_COMMAND_SCHEMA),
        )


@dataclass(frozen=True)
class SourceRef:
    kind: str
    id: str
    frame_id: str | None = None
    observation_id: str | None = None
    plugin_id: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        allowed = {
            "observation",
            "memory_record",
            "pattern",
            "projection",
            "capability",
        }
        if self.kind not in allowed:
            raise ValueError(f"invalid SourceRef.kind {self.kind!r}")
        object.__setattr__(
            self,
            "id",
            require_code_point_len(str(self.id), field_name="id", max_len=MAX_SOURCE_REF_ID),
        )
        object.__setattr__(
            self,
            "note",
            require_code_point_len(
                str(self.note), field_name="note", max_len=MAX_SOURCE_REF_NOTE
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "frame_id": self.frame_id,
            "observation_id": self.observation_id,
            "plugin_id": self.plugin_id,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRef":
        return cls(
            kind=str(data.get("kind") or ""),
            id=str(data.get("id") or ""),
            frame_id=(
                str(data["frame_id"]) if data.get("frame_id") is not None else None
            ),
            observation_id=(
                str(data["observation_id"])
                if data.get("observation_id") is not None
                else None
            ),
            plugin_id=(
                str(data["plugin_id"]) if data.get("plugin_id") is not None else None
            ),
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True)
class ActionProposal:
    """Bounded, serializable action proposal from one plugin for one cycle."""

    plugin_id: str
    frame_id: str
    lifecycle: str
    freshness: str
    confidence: float
    reason: str
    command: ProposedVehicleCommand | None
    assumptions: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()
    available: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    proposal_id: str = ""
    schema: str = ACTION_PROPOSAL_SCHEMA

    def __post_init__(self) -> None:
        plugin_id = require_ascii_id(self.plugin_id, field_name="plugin_id")
        frame_id = require_ascii_id(self.frame_id, field_name="frame_id")
        object.__setattr__(self, "plugin_id", plugin_id)
        object.__setattr__(self, "frame_id", frame_id)
        expected_id = proposal_id_for(plugin_id, frame_id)
        proposal_id = self.proposal_id or expected_id
        if proposal_id != expected_id:
            raise ValueError(
                f"proposal_id must be {expected_id!r}; got {proposal_id!r}"
            )
        object.__setattr__(self, "proposal_id", proposal_id)
        if self.lifecycle not in LIFECYCLES:
            raise ValueError(f"invalid lifecycle {self.lifecycle!r}")
        if self.freshness not in FRESHNESS_VALUES:
            raise ValueError(f"invalid freshness {self.freshness!r}")
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError("confidence must be numeric") from exc
        if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
            raise ValueError("confidence must be finite and in [0, 1]")
        object.__setattr__(self, "confidence", confidence)
        reason = require_code_point_len(
            str(self.reason), field_name="reason", max_len=MAX_REASON
        )
        if not reason and self.lifecycle == "error":
            raise ValueError("error lifecycle requires a non-empty reason")
        object.__setattr__(self, "reason", reason)

        allowed_freshness, available, command_required, min_refs = _MATRIX[self.lifecycle]
        if self.freshness != allowed_freshness:
            raise ValueError(
                f"lifecycle {self.lifecycle} requires freshness {allowed_freshness}; "
                f"got {self.freshness}"
            )
        if bool(self.available) != available:
            raise ValueError(
                f"lifecycle {self.lifecycle} requires available={available}"
            )
        object.__setattr__(self, "available", available)
        if command_required:
            if not isinstance(self.command, ProposedVehicleCommand):
                raise ValueError(
                    f"lifecycle {self.lifecycle} requires a ProposedVehicleCommand"
                )
        else:
            if self.command is not None:
                raise ValueError(
                    f"lifecycle {self.lifecycle} requires command=null"
                )

        assumptions = tuple(str(item) for item in self.assumptions)
        if len(assumptions) > MAX_ASSUMPTIONS:
            raise ValueError(f"assumptions exceed {MAX_ASSUMPTIONS}")
        for item in assumptions:
            require_code_point_len(item, field_name="assumption", max_len=MAX_ASSUMPTION)
        object.__setattr__(self, "assumptions", assumptions)

        refs = tuple(self.source_refs)
        if len(refs) > MAX_SOURCE_REFS:
            raise ValueError(f"source_refs exceed {MAX_SOURCE_REFS}")
        if len(refs) < min_refs:
            raise ValueError(
                f"lifecycle {self.lifecycle} requires at least {min_refs} source_refs"
            )
        for ref in refs:
            if not isinstance(ref, SourceRef):
                raise TypeError("source_refs must contain SourceRef")
        object.__setattr__(self, "source_refs", refs)

        metadata = deepcopy(dict(self.metadata))
        if canonical_json_bytes(metadata) > MAX_PROPOSAL_METADATA_BYTES:
            raise ValueError(
                f"proposal metadata exceeds {MAX_PROPOSAL_METADATA_BYTES} bytes"
            )
        object.__setattr__(self, "metadata", metadata)
        if self.schema != ACTION_PROPOSAL_SCHEMA:
            raise ValueError("invalid ActionProposal schema")
        size = canonical_json_bytes(self.to_dict())
        if size > MAX_PROPOSAL_BYTES:
            raise ValueError(
                f"ActionProposal serializes to {size} bytes; max {MAX_PROPOSAL_BYTES}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "proposal_id": self.proposal_id,
            "plugin_id": self.plugin_id,
            "frame_id": self.frame_id,
            "lifecycle": self.lifecycle,
            "freshness": self.freshness,
            "confidence": self.confidence,
            "reason": self.reason,
            "command": self.command.to_dict() if self.command is not None else None,
            "assumptions": list(self.assumptions),
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "available": self.available,
            "metadata": deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionProposal":
        command_data = data.get("command")
        command = (
            ProposedVehicleCommand.from_dict(command_data)
            if isinstance(command_data, dict)
            else None
        )
        refs = tuple(
            SourceRef.from_dict(item)
            for item in (data.get("source_refs") or ())
            if isinstance(item, dict)
        )
        return cls(
            plugin_id=str(data.get("plugin_id") or ""),
            frame_id=str(data.get("frame_id") or ""),
            lifecycle=str(data.get("lifecycle") or ""),
            freshness=str(data.get("freshness") or ""),
            confidence=float(data.get("confidence") or 0.0),
            reason=str(data.get("reason") or ""),
            command=command,
            assumptions=tuple(str(item) for item in (data.get("assumptions") or ())),
            source_refs=refs,
            available=bool(data.get("available")),
            metadata=deepcopy(dict(data.get("metadata") or {})),
            proposal_id=str(data.get("proposal_id") or ""),
            schema=str(data.get("schema") or ACTION_PROPOSAL_SCHEMA),
        )


def synthetic_error_proposal(
    *,
    plugin_id: str,
    frame_id: str,
    reason: str,
) -> ActionProposal:
    """Bounded synthetic error candidate for runner admission failures."""

    return ActionProposal(
        plugin_id=plugin_id,
        frame_id=frame_id,
        lifecycle="error",
        freshness="none",
        confidence=0.0,
        reason=reason,
        command=None,
        assumptions=(),
        source_refs=(),
        available=False,
        metadata={},
    )
