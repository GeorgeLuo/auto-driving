"""Read plugin history fields back from a shared-memory map.

History can be seeded by hand, so every plugin checks its fields the same way:
an integer is an ``int`` that is not a ``bool``, and a number is a finite int
or float. Each reader returns None for a field that fails.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def history_int(value: Any) -> int | None:
    return value if type(value) is int else None


def history_float(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def history_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    numbers = tuple(history_float(item) for item in value)
    return None if None in numbers else numbers


def next_history_id(recorded: Any, restored_ids: Iterable[int], *, first: int = 1) -> int:
    """The id after every restored one, never below a recorded integer counter."""

    counter = history_int(recorded)
    floor = max(restored_ids, default=first - 1) + 1
    return floor if counter is None else max(counter, floor)
