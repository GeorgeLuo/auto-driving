from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ViewLocation:
    """Image-space or derived-frame location for one evidence record."""

    frame: str
    zone: str
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None
    polygon_xy_norm: tuple[tuple[float, float], ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ViewLocation":
        bbox = data.get("bbox_xyxy_norm")
        polygon = data.get("polygon_xy_norm")
        return cls(
            frame=str(data.get("frame") or "unknown"),
            zone=str(data.get("zone") or "unknown"),
            bbox_xyxy_norm=(
                tuple(float(value) for value in bbox)
                if isinstance(bbox, (list, tuple))
                else None
            ),
            polygon_xy_norm=_normalized_polygon(polygon),
        )


@dataclass(frozen=True)
class PerceivedThing:
    """One spatial evidence record; identity is local unless explicitly stated."""

    thing_id: str
    kind: str
    label: str
    location: ViewLocation
    confidence: float
    properties: dict[str, Any] = field(default_factory=dict)
    source_plugin_id: str | None = None

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
            source_plugin_id=(
                str(data["source_plugin_id"])
                if data.get("source_plugin_id") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class PerceptionSignal:
    """One structured scalar or boolean observation emitted by a plugin."""

    signal_id: str
    value: bool | int | float | str | None
    confidence: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    source_plugin_id: str | None = None

    def __post_init__(self) -> None:
        if not self.signal_id:
            raise ValueError("perception signal ids must be non-empty")
        object.__setattr__(
            self,
            "confidence",
            max(0.0, min(1.0, float(self.confidence))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceptionSignal":
        return cls(
            signal_id=str(data.get("signal_id") or "unknown"),
            value=data.get("value"),
            confidence=float(data.get("confidence") or 0.0),
            properties=dict(data.get("properties") or {}),
            source_plugin_id=(
                str(data["source_plugin_id"])
                if data.get("source_plugin_id") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class PerceptionEvidenceBatch:
    """The narrow output of one plugin's algorithm implementation."""

    signals: tuple[PerceptionSignal, ...] = ()
    things: tuple[PerceivedThing, ...] = ()
    measurements: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        signal_ids = [signal.signal_id for signal in self.signals]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("a plugin evidence batch cannot repeat signal ids")


def _normalized_polygon(value: Any) -> tuple[tuple[float, float], ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        points.append((float(point[0]), float(point[1])))
    return tuple(points)
