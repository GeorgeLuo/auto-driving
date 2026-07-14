from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TypeVar, runtime_checkable

from autonomy.vehicle import SensorReading, SensorSnapshot

from .evidence import PerceivedThing, PerceptionSignal
from .plugin import PerceptionComponentUnavailable


PERCEPTION_TEXT_SCHEMA = "perception_text_v2"
PLUGIN_RESULT_STATUSES = ("ok", "empty", "warming_up", "unavailable", "error")
PluginResultStatus = Literal["ok", "empty", "warming_up", "unavailable", "error"]
ComponentT = TypeVar("ComponentT")


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
    """Structured perception evidence with a framework-rendered text view."""

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


@dataclass
class PerceptionRequest:
    """Framework request used to resolve shared components for plugins."""

    snapshot: SensorSnapshot
    output_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _components: dict[str, Any] = field(default_factory=dict, repr=False)
    _component_errors: dict[str, str] = field(default_factory=dict, repr=False)

    def sensor(self, sensor_id: str) -> SensorReading | None:
        return self.snapshot.readings.get(sensor_id)

    def resolve_component(
        self,
        component_id: str,
        provider: Callable[[], ComponentT],
    ) -> ComponentT | None:
        """Resolve one derived input once and share it across interested plugins."""

        if component_id in self._components:
            return self._components[component_id]
        if component_id in self._component_errors:
            return None
        try:
            component = provider()
        except PerceptionComponentUnavailable as exc:
            self._component_errors[component_id] = str(exc)
            return None
        if component is None:
            self._component_errors[component_id] = "component provider returned no value"
            return None
        self._components[component_id] = component
        return component

    def component(self, component_id: str) -> Any | None:
        return self._components.get(component_id)

    def component_error(self, component_id: str) -> str | None:
        return self._component_errors.get(component_id)

    def component_summary(self) -> dict[str, Any]:
        return {
            "available": {
                component_id: type(component).__name__
                for component_id, component in sorted(self._components.items())
            },
            "errors": dict(sorted(self._component_errors.items())),
        }


@runtime_checkable
class PerceptionMapper(Protocol):
    """Self-contained mapper from vehicle sensors to perception evidence."""

    plugin_id: str

    def reset(self) -> None:
        ...

    def describe_schema(self) -> dict[str, Any]:
        ...

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        ...
