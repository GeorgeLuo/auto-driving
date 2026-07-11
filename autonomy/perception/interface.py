from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from autonomy.vehicle import SensorSnapshot


PERCEPTION_TEXT_SCHEMA = "perception_text_v0"


@dataclass(frozen=True)
class ViewLocation:
    """Computational location for a perceived item."""

    frame: str
    zone: str
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


@dataclass(frozen=True)
class PerceptionText:
    """Line-oriented perception output for decision code and logs."""

    schema: str
    plugin_id: str
    lines: tuple[str, ...]
    things: tuple[PerceivedThing, ...]
    confidence: float
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


@dataclass(frozen=True)
class PerceptionRequest:
    """Input envelope for a perception mapper."""

    snapshot: SensorSnapshot
    output_dir: Path | None = None
    previous_snapshot: SensorSnapshot | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PerceptionMapper(Protocol):
    """Self-contained mapper from vehicle sensors to perception text."""

    plugin_id: str

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        ...
