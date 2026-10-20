"""Commands held between capture times on the runtime clock.

Delivery time is the command boundary, not a measurement of actuation latency.
Intervals are half-open: a delivery at the end belongs to the next interval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HeldCommand:
    steering: float
    throttle: float
    from_ms: int
    to_ms: int


def commands_over(
    history: tuple[dict[str, Any], ...], start_ms: int, end_ms: int,
) -> tuple[HeldCommand, ...] | None:
    """Slice oldest-first deliveries; return None if the start is unknown.

    The last delivery at or before ``start_ms`` supplies the initial command.
    A bounded history cannot establish commands before its oldest delivery.
    """
    if end_ms < start_ms:
        raise ValueError("end_ms must not precede start_ms")
    held = None
    position = 0
    for position, delivery in enumerate(history):
        if delivery["delivered_at_ms"] > start_ms:
            break
        held = delivery
    else:
        position = len(history)
    if held is None:
        return None
    segments = []
    cursor = start_ms
    for delivery in history[position:]:
        at_ms = delivery["delivered_at_ms"]
        if at_ms >= end_ms:
            break
        if at_ms > cursor:
            segments.append(HeldCommand(held["steering"], held["throttle"], cursor, at_ms))
        held = delivery
        cursor = at_ms
    if cursor < end_ms:
        segments.append(HeldCommand(held["steering"], held["throttle"], cursor, end_ms))
    return tuple(segments)
