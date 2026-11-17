"""The perception step's output and the request-level perception boundary.

``PerceptionText`` is current evidence, with one ``PerceptionPluginRun`` per
plugin. ``PerceptionBackend`` is anything that runs perception on a
``PerceptionRequest``, such as the step's ``PerceptionRunner``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
from typing import Any, Literal, Protocol, runtime_checkable

from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.decision_cycle.perception.evidence.rendering import render_perception_text
from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, PerceptionSignal
from autonomy.decision_cycle.runner import FailurePolicy
from autonomy.shared_memory import SharedMemory
from autonomy.fields import (
    FieldError, require_ascii_id, require_choice, require_keys, require_nested,
    require_number, require_safe_int, require_schema, require_text,
)


PERCEPTION_TEXT_SCHEMA = "perception_text"
PLUGIN_RESULT_STATUSES = ("ok", "empty", "warming_up", "unavailable", "error")
PluginResultStatus = Literal["ok", "empty", "warming_up", "unavailable", "error"]

# A perception plugin error is isolated, an explicit reset error propagates,
# and a missing feed skips that plugin. A selection change constructs new runners.
# A stateful plugin is also reset on a missing feed, inside
# frame execution, so an exception there follows ``update`` and becomes that
# plugin's error result.
FAILURE_POLICY = FailurePolicy(
    update="isolate_plugin",
    reset="propagate",
    missing_input="skip_plugin",
)


@dataclass(frozen=True)
class PerceptionPluginRun:
    """Framework-derived execution record for one plugin invocation."""

    plugin_id: str
    status: PluginResultStatus
    duration_ms: float
    signal_count: int
    thing_count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceptionPluginRun":
        document = require_keys(data, ("plugin_id", "status", "duration_ms", "signal_count", "thing_count", "error"))
        error = document["error"]
        return cls(
            plugin_id=require_ascii_id(document["plugin_id"], field_name="plugin_id"),
            status=require_choice(document["status"], PLUGIN_RESULT_STATUSES, field_name="status"),
            duration_ms=require_number(document["duration_ms"], field_name="duration_ms", bounds=(0, float("inf"))),
            signal_count=require_safe_int(document["signal_count"], field_name="signal_count"),
            thing_count=require_safe_int(document["thing_count"], field_name="thing_count"),
            error=require_text(error, field_name="error") if error is not None else None,
        )


@dataclass(frozen=True)
class PerceptionText:
    """Current evidence for the perception step, with a rendered text view.

    The framework builds this from plugin evidence batches.
    """

    schema: str
    plugin_id: str
    status: str
    signals: tuple[PerceptionSignal, ...]
    things: tuple[PerceivedThing, ...]
    plugin_runs: tuple[PerceptionPluginRun, ...] = ()
    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)
    limits: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Text rendered from the evidence. Recordings and reports do not store it."""

        return render_perception_text(
            schema=self.schema,
            plugin_id=self.plugin_id,
            plugin_runs=self.plugin_runs,
            signals=self.signals,
            things=self.things,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "plugin_id": self.plugin_id, "status": self.status,
            "signals": tuple(signal.to_dict() for signal in self.signals),
            "things": tuple(thing.to_dict() for thing in self.things),
            "plugin_runs": tuple(run.to_dict() for run in self.plugin_runs),
            "measurements": deepcopy(self.measurements), "limits": self.limits,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceptionText":
        document = require_schema(data, PERCEPTION_TEXT_SCHEMA, (
            "schema", "plugin_id", "status", "signals", "things", "plugin_runs", "measurements", "limits",
        ))
        parsed = {}
        for name, parse in (("signals", PerceptionSignal.from_dict), ("things", PerceivedThing.from_dict),
                            ("plugin_runs", PerceptionPluginRun.from_dict)):
            values = document[name]
            if not isinstance(values, (list, tuple)):
                raise FieldError(name, "must be an array")
            parsed[name] = tuple(require_nested(parse, value, field_name=f"{name}.{index}")
                                 for index, value in enumerate(values))
        measurements = document["measurements"]
        if not isinstance(measurements, dict):
            raise FieldError("measurements", "must be an object")
        for plugin_id, values in measurements.items():
            require_ascii_id(plugin_id, field_name=f"measurements.{plugin_id}")
            if not isinstance(values, dict):
                raise FieldError(f"measurements.{plugin_id}", "must be an object")
        limits = document["limits"]
        if not isinstance(limits, (list, tuple)):
            raise FieldError("limits", "must be an array")
        return cls(
            schema=document["schema"],
            plugin_id=require_ascii_id(document["plugin_id"], field_name="plugin_id"),
            status=require_choice(document["status"], (*PLUGIN_RESULT_STATUSES, "partial"), field_name="status"),
            **parsed, measurements=measurements,
            limits=tuple(require_text(value, field_name=f"limits.{index}") for index, value in enumerate(limits)),
        )


@runtime_checkable
class PerceptionBackend(Protocol):
    """Run perception on one request: sensors in, current evidence out."""

    plugin_id: str

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        ...

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        ...
