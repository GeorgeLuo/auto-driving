from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ViewLocation:
    """Image-space or derived-frame location for one evidence record."""

    frame: str
    zone: str
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None
    polygon_xy_norm: tuple[tuple[float, float], ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "bbox_xyxy_norm",
            _normalized_bbox(self.bbox_xyxy_norm),
        )
        object.__setattr__(
            self,
            "polygon_xy_norm",
            _normalized_polygon(self.polygon_xy_norm),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ViewLocation":
        return cls(
            frame=str(data.get("frame") or "unknown"),
            zone=str(data.get("zone") or "unknown"),
            bbox_xyxy_norm=data.get("bbox_xyxy_norm"),
            polygon_xy_norm=data.get("polygon_xy_norm"),
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

    def __post_init__(self) -> None:
        _require_identifier(self.thing_id, record_name="perception thing")
        object.__setattr__(
            self,
            "confidence",
            _normalized_confidence(self.confidence, record_name="perception thing"),
        )

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
            properties=deepcopy(dict(data.get("properties") or {})),
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
        _require_identifier(self.signal_id, record_name="perception signal")
        object.__setattr__(
            self,
            "confidence",
            _normalized_confidence(self.confidence, record_name="perception signal"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceptionSignal":
        return cls(
            signal_id=str(data.get("signal_id") or "unknown"),
            value=data.get("value"),
            confidence=float(data.get("confidence") or 0.0),
            properties=deepcopy(dict(data.get("properties") or {})),
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
        thing_ids = [thing.thing_id for thing in self.things]
        if len(thing_ids) != len(set(thing_ids)):
            raise ValueError("a plugin evidence batch cannot repeat thing ids")


def _require_identifier(value: Any, *, record_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{record_name} ids must be non-empty strings")


def _finite_float(value: Any, *, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    return normalized


def _normalized_confidence(value: Any, *, record_name: str) -> float:
    confidence = _finite_float(value, field_name=f"{record_name} confidence")
    return max(0.0, min(1.0, confidence))


def _normalized_coordinate(value: Any) -> float:
    coordinate = _finite_float(value, field_name="normalized coordinate")
    if coordinate < 0.0 or coordinate > 1.0:
        raise ValueError("normalized coordinates must be between 0.0 and 1.0")
    return coordinate


def _normalized_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("normalized bounding boxes must contain four coordinates")
    x1, y1, x2, y2 = tuple(_normalized_coordinate(item) for item in value)
    if x1 > x2 or y1 > y2:
        raise ValueError("normalized bounding boxes must have ordered corners")
    return (x1, y1, x2, y2)


def _normalized_polygon(value: Any) -> tuple[tuple[float, float], ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError("normalized polygons must contain at least three points")
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("normalized polygon points must contain two coordinates")
        points.append(
            (
                _normalized_coordinate(point[0]),
                _normalized_coordinate(point[1]),
            )
        )
    return tuple(points)
