from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TypeVar, runtime_checkable

from autonomy.vehicle import SensorReading, SensorSnapshot


PERCEPTION_TEXT_SCHEMA = "perception_text_v1"
PLUGIN_RESULT_STATUSES = ("ok", "empty", "warming_up", "unavailable", "error")
PLUGIN_STATE_MODES = ("stateless", "pairwise", "windowed")

PluginResultStatus = Literal["ok", "empty", "warming_up", "unavailable", "error"]
PluginStateMode = Literal["stateless", "pairwise", "windowed"]
ComponentT = TypeVar("ComponentT")


class PerceptionComponentUnavailable(RuntimeError):
    """A plugin-requested component cannot be derived from this snapshot."""


@dataclass(frozen=True)
class PerceptionPluginContract:
    """Machine-readable execution requirements for one plugin."""

    required_components: tuple[str, ...] = ()
    state_mode: PluginStateMode = "stateless"
    artifact_policy: Literal["none", "optional", "required"] = "none"

    def __post_init__(self) -> None:
        if any(not isinstance(component, str) or not component for component in self.required_components):
            raise ValueError("required component ids must be non-empty strings")
        if len(set(self.required_components)) != len(self.required_components):
            raise ValueError("required component ids must be unique")
        if self.state_mode not in PLUGIN_STATE_MODES:
            raise ValueError(f"unsupported plugin state mode: {self.state_mode!r}")
        if self.artifact_policy not in {"none", "optional", "required"}:
            raise ValueError(f"unsupported artifact policy: {self.artifact_policy!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ViewLocation:
    """Computational location for a perceived item."""

    frame: str
    zone: str
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ViewLocation":
        bbox = data.get("bbox_xyxy_norm")
        return cls(
            frame=str(data.get("frame") or "unknown"),
            zone=str(data.get("zone") or "unknown"),
            bbox_xyxy_norm=tuple(float(value) for value in bbox) if isinstance(bbox, (list, tuple)) else None,
        )


@dataclass(frozen=True)
class PerceivedThing:
    """One thing-like observation produced by a perception mapper."""

    thing_id: str
    kind: str
    label: str
    location: ViewLocation
    confidence: float
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceivedThing":
        location = data.get("location") if isinstance(data.get("location"), dict) else {}
        return cls(
            thing_id=str(data.get("thing_id") or "unknown"),
            kind=str(data.get("kind") or "unknown"),
            label=str(data.get("label") or "unknown"),
            location=ViewLocation.from_dict(location),
            confidence=float(data.get("confidence") or 0.0),
            properties=dict(data.get("properties") or {}),
        )


@dataclass(frozen=True)
class PerceptionPluginResult:
    """One plugin's evidence before mapper-level aggregation."""

    status: PluginResultStatus = "ok"
    lines: tuple[str, ...] = ()
    things: tuple[PerceivedThing, ...] = ()
    observations: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    limits: tuple[str, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in PLUGIN_RESULT_STATUSES:
            raise ValueError(f"unsupported plugin result status: {self.status!r}")
        if self.status == "error" and not self.error:
            raise ValueError("error plugin results must include an error message")


@dataclass(frozen=True)
class PerceptionPluginRun:
    plugin_id: str
    status: PluginResultStatus
    duration_ms: float
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
            thing_count=int(data.get("thing_count") or 0),
            artifact_count=int(data.get("artifact_count") or 0),
            error=str(data["error"]) if data.get("error") is not None else None,
        )


@dataclass(frozen=True)
class PerceptionText:
    """Line-oriented perception output for decision code and logs."""

    schema: str
    plugin_id: str
    status: str
    lines: tuple[str, ...]
    things: tuple[PerceivedThing, ...]
    confidence: float
    plugin_runs: tuple[PerceptionPluginRun, ...] = ()
    observations: dict[str, Any] = field(default_factory=dict)
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
            things=tuple(
                PerceivedThing.from_dict(item)
                for item in data.get("things") or ()
                if isinstance(item, dict)
            ),
            confidence=float(data.get("confidence") or 0.0),
            plugin_runs=tuple(
                PerceptionPluginRun.from_dict(item)
                for item in data.get("plugin_runs") or ()
                if isinstance(item, dict)
            ),
            observations=dict(data.get("observations") or {}),
            artifacts={str(key): str(value) for key, value in dict(data.get("artifacts") or {}).items()},
            limits=tuple(str(item) for item in data.get("limits") or ()),
        )


@dataclass
class PerceptionRequest:
    """Generic component context shared by configured perception plugins."""

    snapshot: SensorSnapshot
    output_dir: Path | None = None
    previous_snapshot: SensorSnapshot | None = None
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
class PerceptionPlugin(Protocol):
    plugin_id: str
    contract: PerceptionPluginContract

    def reset(self) -> None:
        ...

    def describe_schema(self) -> dict[str, Any]:
        ...

    def perceive(self, request: PerceptionRequest) -> PerceptionPluginResult:
        ...


@runtime_checkable
class PerceptionMapper(Protocol):
    """Self-contained mapper from vehicle sensors to perception text."""

    plugin_id: str

    def reset(self) -> None:
        ...

    def describe_schema(self) -> dict[str, Any]:
        ...

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        ...
