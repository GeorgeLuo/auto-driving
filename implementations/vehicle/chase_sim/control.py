"""Typed actor controls at the Chase protocol boundary."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ChaseControl:
    source: str
    steering: float
    forward: bool | None
    reverse: bool | None
    motion: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"source": self.source, "steering": self.steering}
        if self.motion is not None:
            result["motion"] = self.motion
        if self.forward is not None and self.reverse is not None:
            result.update(forward=self.forward, reverse=self.reverse)
        return result


def validate_chase_control(
    value: object,
    *,
    allow_motion: bool = False,
    bounded_steering: bool = True,
) -> ChaseControl | None:
    """Session inputs allow motion-only input and any finite steering.

    Evaluator controls require both direction flags and steering in [-1, 1].
    """
    if not isinstance(value, dict):
        return None
    source = value.get("source")
    if not isinstance(source, str) or not source.strip():
        return None
    directions = _direction_flags(value.get("forward"), value.get("reverse"))
    if not allow_motion and directions is None:
        return None
    steering = value.get("steering")
    if isinstance(steering, bool) or not isinstance(steering, (int, float)):
        return None
    steering = float(steering)
    if not math.isfinite(steering) or (bounded_steering and not -1.0 <= steering <= 1.0):
        return None
    motion = value.get("motion") if allow_motion else None
    motion = motion.strip() if isinstance(motion, str) and motion.strip() else None
    if directions is None:
        if motion is None:
            return None
        forward = reverse = None
    else:
        forward, reverse = directions
    return ChaseControl(source.strip(), steering, forward, reverse, motion)


def _direction_flags(forward: object, reverse: object) -> tuple[bool, bool] | None:
    if isinstance(forward, bool) and isinstance(reverse, bool):
        return forward, reverse
    return None
