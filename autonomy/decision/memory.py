from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@dataclass
class FrameMemory:
    frame_id: str
    image_path: str
    timestamp_ms: int
    sensor_source: str
    command_before_frame: dict[str, Any] | None
    observation: dict[str, Any]


@dataclass
class KeyframeMemory:
    frame_id: str
    image_path: str
    reason: str
    score: float
    timestamp_ms: int
    observation: dict[str, Any]


@dataclass
class AutonomyRunMemory:
    run_id: str
    run_type: str
    created_at_ms: int
    vehicle_source: str
    default_sensor_source: str
    frames: list[FrameMemory] = field(default_factory=list)
    keyframes: list[KeyframeMemory] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def add_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append({
            "type": event_type,
            "timestamp_ms": timestamp_ms(),
            **(payload or {}),
        })

    def add_frame(self, frame: FrameMemory) -> None:
        self.frames.append(frame)

    def add_keyframe(
        self,
        *,
        frame: FrameMemory,
        reason: str,
        score: float,
    ) -> None:
        self.keyframes.append(
            KeyframeMemory(
                frame_id=frame.frame_id,
                image_path=frame.image_path,
                reason=reason,
                score=float(score),
                timestamp_ms=timestamp_ms(),
                observation=frame.observation,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: Path) -> None:
        write_json(path, self.to_dict())
