"""The perception step's output and the request-level perception boundary.

``PerceptionText`` is current evidence, with one ``PerceptionPluginRun`` per
plugin. ``PerceptionBackend`` is anything that runs perception on a
``PerceptionRequest``, such as the step's ``PerceptionRunner``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, PerceptionSignal
from autonomy.shared_memory import SharedMemory


PERCEPTION_TEXT_SCHEMA = "perception_text_v2"
PERCEPTION_SCHEMA = "perception_schema_v3"
PLUGIN_RESULT_STATUSES = ("ok", "empty", "warming_up", "unavailable", "error")
PluginResultStatus = Literal["ok", "empty", "warming_up", "unavailable", "error"]

# Same field names as memory's failure policy. The values differ: a perception
# plugin error is isolated, a reset error propagates, and a missing feed skips
# that plugin.
FAILURE_POLICY_FIELDS = ("update", "reset", "missing_input")
UPDATE_FAILURE = "isolate_plugin"
RESET_FAILURE = "propagate"
MISSING_INPUT = "skip_plugin"


def failure_policy() -> dict[str, str]:
    """The values ``PerceptionRunner`` reads when a plugin fails or a feed is missing."""

    return {
        "update": UPDATE_FAILURE,
        "reset": RESET_FAILURE,
        "missing_input": MISSING_INPUT,
    }


def composition_declaration() -> dict[str, str]:
    """How several perception plugins share one frame."""

    return {
        "order": "selection_order",
        "partial_result": "partial when some plugins fail and some still produce evidence",
    }


@dataclass(frozen=True)
class PerceptionPluginRun:
    """Framework-derived execution record for one plugin invocation."""

    plugin_id: str
    status: PluginResultStatus
    duration_ms: float
    signal_count: int
    thing_count: int
    artifact_count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceptionPluginRun":
        return cls(
            plugin_id=str(data.get("plugin_id") or "unknown"),
            status=str(data.get("status") or "error"),
            duration_ms=float(data.get("duration_ms") or 0.0),
            signal_count=int(data.get("signal_count") or 0),
            thing_count=int(data.get("thing_count") or 0),
            artifact_count=int(data.get("artifact_count") or 0),
            error=str(data["error"]) if data.get("error") is not None else None,
        )


@dataclass(frozen=True)
class PerceptionText:
    """Current evidence for the perception step, with a rendered text view.

    The framework builds this from plugin evidence batches.
    """

    schema: str
    plugin_id: str
    status: str
    lines: tuple[str, ...]
    signals: tuple[PerceptionSignal, ...]
    things: tuple[PerceivedThing, ...]
    plugin_runs: tuple[PerceptionPluginRun, ...] = ()
    measurements: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    limits: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["text"] = self.text
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceptionText":
        return cls(
            schema=str(data.get("schema") or PERCEPTION_TEXT_SCHEMA),
            plugin_id=str(data.get("plugin_id") or "unknown"),
            status=str(data.get("status") or "error"),
            lines=tuple(str(line) for line in data.get("lines") or ()),
            signals=tuple(
                PerceptionSignal.from_dict(item)
                for item in data.get("signals") or ()
                if isinstance(item, dict)
            ),
            things=tuple(
                PerceivedThing.from_dict(item)
                for item in data.get("things") or ()
                if isinstance(item, dict)
            ),
            plugin_runs=tuple(
                PerceptionPluginRun.from_dict(item)
                for item in data.get("plugin_runs") or ()
                if isinstance(item, dict)
            ),
            measurements={
                str(plugin_id): dict(values)
                for plugin_id, values in dict(data.get("measurements") or {}).items()
                if isinstance(values, dict)
            },
            artifacts={
                str(key): str(value)
                for key, value in dict(data.get("artifacts") or {}).items()
            },
            limits=tuple(str(item) for item in data.get("limits") or ()),
        )


@runtime_checkable
class PerceptionBackend(Protocol):
    """Run perception on one request: sensors in, current evidence out."""

    plugin_id: str

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        ...

    def describe_schema(self) -> dict[str, Any]:
        ...

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        ...
