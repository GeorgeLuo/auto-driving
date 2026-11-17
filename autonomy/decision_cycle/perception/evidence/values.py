from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections.abc import Mapping
from typing import Any

from autonomy.decision_cycle.confidence import optional_confidence
from autonomy.serialization import FrozenJsonObject, deep_freeze, frozen_mapping_to_dict
from autonomy.fields import (
    FieldError, require_ascii_id, require_keys, require_nested, require_number, require_text,
)


@dataclass(frozen=True)
class ViewLocation:
    """Image-space or derived-frame location for one evidence record."""

    frame: str
    zone: str
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None
    polygon_xy_norm: tuple[tuple[float, float], ...] | None = None

    def __post_init__(self) -> None:
        require_ascii_id(self.frame, field_name="frame")
        require_ascii_id(self.zone, field_name="zone")
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
        document = require_keys(data, ("frame", "zone", "bbox_xyxy_norm", "polygon_xy_norm"))
        return cls(**document)


@dataclass(frozen=True)
class PerceivedThing:
    """One spatial evidence record; identity is local unless explicitly stated."""

    thing_id: str
    kind: str
    label: str
    location: ViewLocation
    confidence: float | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)
    source_plugin_id: str | None = None

    def __post_init__(self) -> None:
        require_ascii_id(self.thing_id, field_name="thing_id")
        require_ascii_id(self.kind, field_name="kind")
        require_text(self.label, field_name="label")
        if not isinstance(self.location, ViewLocation):
            raise FieldError("location", "must be ViewLocation")
        if self.source_plugin_id is not None:
            require_ascii_id(self.source_plugin_id, field_name="source_plugin_id")
        if not isinstance(self.properties, (dict, FrozenJsonObject)):
            raise FieldError("properties", "must be an object")
        if not isinstance(self.properties, FrozenJsonObject):
            try:
                object.__setattr__(self, "properties", deep_freeze(self.properties, field_name="properties"))
            except ValueError as exc:
                raise FieldError("properties", str(exc)) from exc
        object.__setattr__(
            self,
            "confidence",
            optional_confidence(
                self.confidence, field_name="perception thing confidence", clamp=True
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "thing_id": self.thing_id, "kind": self.kind, "label": self.label,
            "location": self.location.to_dict(), "confidence": self.confidence,
            "properties": frozen_mapping_to_dict(self.properties), "source_plugin_id": self.source_plugin_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceivedThing":
        document = require_keys(data, (
            "thing_id", "kind", "label", "location", "confidence", "properties", "source_plugin_id",
        ))
        confidence = document["confidence"]
        if confidence is not None:
            confidence = require_number(confidence, field_name="confidence", bounds=(0, 1))
        return cls(
            thing_id=document["thing_id"], kind=document["kind"], label=document["label"],
            location=require_nested(ViewLocation.from_dict, document["location"], field_name="location"),
            confidence=confidence, properties=document["properties"],
            source_plugin_id=document["source_plugin_id"],
        )


@dataclass(frozen=True)
class PerceptionSignal:
    """One scalar or boolean evidence signal from a perception plugin."""

    signal_id: str
    value: bool | int | float | str | None
    confidence: float | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)
    source_plugin_id: str | None = None

    def __post_init__(self) -> None:
        require_ascii_id(self.signal_id, field_name="signal_id")
        if self.source_plugin_id is not None:
            require_ascii_id(self.source_plugin_id, field_name="source_plugin_id")
        if not isinstance(self.properties, (dict, FrozenJsonObject)):
            raise FieldError("properties", "must be an object")
        if not isinstance(self.properties, FrozenJsonObject):
            try:
                object.__setattr__(self, "properties", deep_freeze(self.properties, field_name="properties"))
            except ValueError as exc:
                raise FieldError("properties", str(exc)) from exc
        if self.value is not None and not isinstance(self.value, (bool, str)):
            require_number(self.value, field_name="value")
        object.__setattr__(
            self,
            "confidence",
            optional_confidence(
                self.confidence, field_name="perception signal confidence", clamp=True
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id, "value": self.value, "confidence": self.confidence,
            "properties": frozen_mapping_to_dict(self.properties), "source_plugin_id": self.source_plugin_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PerceptionSignal":
        document = require_keys(data, ("signal_id", "value", "confidence", "properties", "source_plugin_id"))
        confidence = document["confidence"]
        if confidence is not None:
            confidence = require_number(confidence, field_name="confidence", bounds=(0, 1))
        return cls(
            signal_id=document["signal_id"], value=document["value"], confidence=confidence,
            properties=document["properties"], source_plugin_id=document["source_plugin_id"],
        )


@dataclass(frozen=True)
class PerceptionEvidenceBatch:
    """Evidence returned by one perception plugin's ``perceive`` call."""

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


def _normalized_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise FieldError("bbox_xyxy_norm", "must contain four coordinates")
    x1, y1, x2, y2 = tuple(require_number(item, field_name=f"bbox_xyxy_norm.{index}", bounds=(0, 1))
                           for index, item in enumerate(value))
    if x1 > x2 or y1 > y2:
        raise FieldError("bbox_xyxy_norm", "must have ordered corners")
    return (x1, y1, x2, y2)


def _normalized_polygon(value: Any) -> tuple[tuple[float, float], ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise FieldError("polygon_xy_norm", "must contain at least three points")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(value):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise FieldError(f"polygon_xy_norm.{index}", "must contain two coordinates")
        points.append(
            (
                require_number(point[0], field_name=f"polygon_xy_norm.{index}.0", bounds=(0, 1)),
                require_number(point[1], field_name=f"polygon_xy_norm.{index}.1", bounds=(0, 1)),
            )
        )
    return tuple(points)
