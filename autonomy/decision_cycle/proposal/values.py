"""Candidate command returned by proposal.

``ActionProposal`` is one plugin's candidate for one cycle, with its command
and supporting evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Literal

from autonomy.decision_cycle.action_identifiers import (
    proposal_id_for,
)
from autonomy.fields import (
    FieldError, require_ascii_id, require_choice, require_keys, require_nested,
    require_number, require_schema, require_text,
)
from autonomy.serialization import (
    FrozenJsonObject,
    deep_freeze,
    frozen_mapping_to_dict,
)

ACTION_PROPOSAL_SCHEMA = "action_proposal"
PROPOSED_VEHICLE_COMMAND_SCHEMA = "proposed_vehicle_command"
MAX_REASON = 240
MAX_ASSUMPTION = 64
MAX_ASSUMPTIONS = 8
MAX_SOURCE_REFS = 16
MAX_SOURCE_REF_ID = 128
MAX_SOURCE_REF_NOTE = 64
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
    """Canonical proposed vehicle command (not applied AutonomyControl).

    Steering and throttle are signed and normalized to -1..1: steering -1 is full left and +1 full
    right, and the throttle's sign is the direction.
    ``gear`` is derived from it (``forward``, ``reverse``, or ``hold`` near zero) for readable
    reports, so a command cannot name a gear that contradicts its throttle.
    """

    steering: float
    throttle: float
    normalized: bool = True
    schema: str = PROPOSED_VEHICLE_COMMAND_SCHEMA

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        require_number(self.steering, field_name="steering", bounds=(-1, 1))
        require_number(self.throttle, field_name="throttle", bounds=(-1, 1))
        if self.normalized is not True:
            raise FieldError("normalized", "must be true")
        require_choice(self.schema, (PROPOSED_VEHICLE_COMMAND_SCHEMA,), field_name="schema")

    @property
    def gear(self) -> str:
        """The throttle's direction: ``forward``, ``reverse``, or ``hold`` when it is near zero."""

        if abs(self.throttle) < COMMAND_EPS:
            return "hold"
        return "forward" if self.throttle > 0 else "reverse"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "steering": self.steering,
            "throttle": self.throttle,
            "gear": self.gear,
            "normalized": True,
        }

    @classmethod
    def from_dict(cls, payload: object) -> ProposedVehicleCommand:
        document = require_schema(payload, PROPOSED_VEHICLE_COMMAND_SCHEMA,
                                  {"schema", "steering", "throttle", "gear", "normalized"})
        command = cls(
            steering=document["steering"], throttle=document["throttle"],
            normalized=document["normalized"], schema=document["schema"],
        )
        gear = require_choice(document["gear"], ("forward", "reverse", "hold"), field_name="gear")
        if gear != command.gear:
            raise FieldError("gear", f"{gear!r} contradicts throttle {command.throttle}")
        return command


@dataclass(frozen=True)
class SourceRef:
    kind: str
    id: str
    frame_id: str | None = None
    observation_id: str | None = None
    plugin_id: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        require_choice(self.kind, ("observation", "memory_record"), field_name="kind")
        # Memory record IDs are composite ledger keys, not 64-character plugin IDs.
        _bounded_text(self.id, field_name="id", max_len=MAX_SOURCE_REF_ID)
        for name in ("frame_id", "observation_id", "plugin_id"):
            value = getattr(self, name)
            if value is not None:
                require_ascii_id(value, field_name=name)
        _bounded_text(self.note, field_name="note", max_len=MAX_SOURCE_REF_NOTE, empty=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "id": self.id,
            "frame_id": self.frame_id,
            "observation_id": self.observation_id,
            "plugin_id": self.plugin_id,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: object) -> SourceRef:
        document = require_keys(payload, {item.name for item in fields(cls)})
        return cls(
            kind=document["kind"], id=document["id"], frame_id=document["frame_id"],
            observation_id=document["observation_id"], plugin_id=document["plugin_id"], note=document["note"],
        )


@dataclass(frozen=True)
class ActionProposal:
    """One proposal plugin's candidate command and supporting evidence."""

    plugin_id: str
    frame_id: str
    lifecycle: str
    freshness: str
    confidence: float | None
    reason: str
    command: ProposedVehicleCommand | None
    assumptions: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()
    available: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)
    proposal_id: str = ""
    schema: str = ACTION_PROPOSAL_SCHEMA

    def __post_init__(self) -> None:
        if self.proposal_id == "":
            object.__setattr__(self, "proposal_id", proposal_id_for(self.plugin_id, self.frame_id))
        # Plugin constructors and returns are runtime boundaries; annotations do not validate them.
        if not isinstance(self.assumptions, (list, tuple)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise FieldError("assumptions", "must be a list or tuple of strings")
        object.__setattr__(self, "assumptions", tuple(self.assumptions))
        if not isinstance(self.source_refs, (list, tuple)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise FieldError("source_refs", "must be a list or tuple of SourceRef")
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        if isinstance(self.metadata, dict):
            try:
                object.__setattr__(self, "metadata", deep_freeze(self.metadata, field_name="metadata"))
            except ValueError as exc:
                raise FieldError("metadata", str(exc)) from exc
        self.validate()

    def validate(self) -> None:
        """Validate a plugin's typed return without exporting or copying it."""
        self._validate_identity()
        self._validate_status()
        self._validate_matrix()
        self._validate_assumptions()
        self._validate_source_refs()
        if not isinstance(self.metadata, FrozenJsonObject):
            raise FieldError("metadata", "must be an immutable JSON object")
        _validate_frozen_json(self.metadata, field_name="metadata")
        require_choice(self.schema, (ACTION_PROPOSAL_SCHEMA,), field_name="schema")

    def _validate_identity(self) -> None:
        plugin_id = require_ascii_id(self.plugin_id, field_name="plugin_id")
        frame_id = require_ascii_id(self.frame_id, field_name="frame_id")
        expected_id = proposal_id_for(plugin_id, frame_id)
        if self.proposal_id != expected_id:
            raise FieldError("proposal_id", f"must be {expected_id!r}; got {self.proposal_id!r}")

    def _validate_status(self) -> None:
        require_choice(self.lifecycle, LIFECYCLES, field_name="lifecycle")
        require_choice(self.freshness, FRESHNESS_VALUES, field_name="freshness")
        if self.confidence is not None:
            require_number(self.confidence, field_name="confidence", bounds=(0, 1))
        reason = _bounded_text(self.reason, field_name="reason", max_len=MAX_REASON)
        if not reason.strip():
            raise FieldError("reason", "must contain non-whitespace text")

    def _validate_matrix(self) -> None:
        allowed_freshness, available, command_required, _min_refs = _MATRIX[self.lifecycle]
        if self.freshness != allowed_freshness:
            raise FieldError("freshness", f"lifecycle {self.lifecycle} requires {allowed_freshness}")
        if type(self.available) is not bool:
            raise FieldError("available", "must be a bool")
        if self.available != available:
            raise FieldError("available", f"lifecycle {self.lifecycle} requires {available}")
        if command_required:
            if not isinstance(self.command, ProposedVehicleCommand):
                raise FieldError("command", f"lifecycle {self.lifecycle} requires a ProposedVehicleCommand")
            require_nested(ProposedVehicleCommand.validate, self.command, field_name="command")
        elif self.command is not None:
            raise FieldError("command", f"lifecycle {self.lifecycle} requires null")

    def _validate_assumptions(self) -> None:
        if not isinstance(self.assumptions, tuple):  # pyright: ignore[reportUnnecessaryIsInstance] - validate plugin returns
            raise FieldError("assumptions", "must be an immutable tuple")
        if len(self.assumptions) > MAX_ASSUMPTIONS:
            raise FieldError("assumptions", f"exceeds {MAX_ASSUMPTIONS}")
        for index, item in enumerate(self.assumptions):
            _bounded_text(item, field_name=f"assumptions.{index}", max_len=MAX_ASSUMPTION, empty=True)

    def _validate_source_refs(self) -> None:
        _freshness, _available, _command_required, min_refs = _MATRIX[self.lifecycle]
        if not isinstance(self.source_refs, tuple):  # pyright: ignore[reportUnnecessaryIsInstance] - validate plugin returns
            raise FieldError("source_refs", "must be an immutable tuple")
        if len(self.source_refs) > MAX_SOURCE_REFS:
            raise FieldError("source_refs", f"exceeds {MAX_SOURCE_REFS}")
        if len(self.source_refs) < min_refs:
            raise FieldError("source_refs", f"lifecycle {self.lifecycle} requires at least {min_refs}")
        for index, ref in enumerate(self.source_refs):
            if not isinstance(ref, SourceRef):  # pyright: ignore[reportUnnecessaryIsInstance] - validate plugin returns
                raise FieldError(f"source_refs.{index}", "must be a SourceRef")
            require_nested(SourceRef.validate, ref, field_name=f"source_refs.{index}")

    def is_active(self, frame_id: str) -> bool:
        """Whether a plan may act on this candidate in the cycle for ``frame_id``.

        A fresh or retained candidate is available and carries a command; it
        is active in the cycle it was proposed for.
        """

        return self.lifecycle in {"fresh", "retained"} and self.frame_id == frame_id

    def to_dict(self) -> dict[str, object]:
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
            "metadata": frozen_mapping_to_dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: object) -> ActionProposal:
        document = require_schema(payload, ACTION_PROPOSAL_SCHEMA, {item.name for item in fields(cls)})
        lifecycle = require_choice(document["lifecycle"], LIFECYCLES, field_name="lifecycle")
        command = document["command"]
        if command is not None:
            if not _MATRIX[lifecycle][2]:
                raise FieldError("command", f"lifecycle {lifecycle} requires null")
            command = require_nested(ProposedVehicleCommand.from_dict, command, field_name="command")
        raw_refs = document["source_refs"]
        if not isinstance(raw_refs, (list, tuple)):
            raise FieldError("source_refs", "must be an array")
        refs = tuple(
            require_nested(SourceRef.from_dict, item, field_name=f"source_refs.{index}")
            for index, item in enumerate(raw_refs)
        )
        metadata = document["metadata"]
        if not isinstance(metadata, dict):
            raise FieldError("metadata", "must be a JSON object")
        return cls(
            plugin_id=document["plugin_id"], frame_id=document["frame_id"],
            lifecycle=lifecycle, freshness=document["freshness"],
            confidence=document["confidence"], reason=document["reason"], command=command,
            assumptions=document["assumptions"], source_refs=refs, available=document["available"],
            metadata=metadata, proposal_id=require_text(document["proposal_id"], field_name="proposal_id"),
            schema=document["schema"],
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


def _bounded_text(value: object, *, field_name: str, max_len: int, empty: bool = False) -> str:
    if empty and value == "":
        return ""
    text = require_text(value, field_name=field_name)
    if len(text) > max_len:
        raise FieldError(field_name, f"exceeds {max_len} code points")
    return text


def _validate_frozen_json(value: object, *, field_name: str) -> None:
    if isinstance(value, FrozenJsonObject):
        for key, item in value.items():
            if not isinstance(key, str):  # pyright: ignore[reportUnnecessaryIsInstance] - validate plugin-owned frozen storage
                raise FieldError(field_name, "JSON object keys must be strings")
            _validate_frozen_json(item, field_name=f"{field_name}.{key}")
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            _validate_frozen_json(item, field_name=f"{field_name}.{index}")
    elif isinstance(value, float):
        require_number(value, field_name=field_name)
    elif value is not None and type(value) not in (str, int, bool):
        raise FieldError(field_name, "must contain immutable JSON values")
